#!/usr/bin/env python3
# coding=utf8
"""
Tekton as a CODE: Main script
"""

import datetime
import http.client
import io
import json
import os
import random
import re
import string
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request

GITHUB_HOST_URL = "api.github.com"
GITHUB_TOKEN = """$(params.github_token)"""

CATALOGS = {
    'official':
    'https://raw.githubusercontent.com/tektoncd/catalog/master/task',
}

CHECK_RUN_ID = None
REPO_FULL_NAME = None


def github_check_set_status(repository_full_name, check_run_id, target_url,
                            conclusion, output):
    """
    Set status on the GitHUB Check
    """
    body = {
        "name": 'tekton-asa-code',
        "status": "completed",
        'conclusion': conclusion,
        'completed_at': datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        'output': output
    }
    if target_url:
        body["details_url"] = target_url

    output = github_request(
        "PATCH",
        f"https://{GITHUB_HOST_URL}/repos/{repository_full_name}/check-runs/{check_run_id}",
        headers={
            "Accept": "application/vnd.github.antiope-preview+json"
        },
        body=body).read().decode()
    return output


# pylint: disable=unnecessary-pass
class CouldNotFindConfigKeyException(Exception):
    """Raise an exception when we cannot find the key string in json"""
    pass


def execute(command, check_error=""):
    """Execute commmand"""
    result = ""
    try:
        result = subprocess.run(['/bin/sh', '-c', command],
                                stdout=subprocess.PIPE,
                                check=True)
    except subprocess.CalledProcessError as exception:
        if check_error:
            raise exception
    return result


# https://stackoverflow.com/a/18422264
def stream(command, filename, check_error=""):
    """Stream command"""
    with io.open(filename, 'wb') as writer, io.open(filename, 'rb',
                                                    1) as reader:
        try:
            process = subprocess.Popen(command.split(" "), stdout=writer)
        except subprocess.CalledProcessError as exception:
            print(check_error)
            raise exception

        while process.poll() is None:
            sys.stdout.write(reader.read().decode())
            time.sleep(0.5)
        # Read the remaining
        sys.stdout.write(reader.read().decode())


def github_request(method, url, headers=None, body=None):
    """Execute a request to the GitHUB API, handling redirect"""
    if not headers:
        headers = {}
    headers.update({
        "User-Agent": "TektonCD, the peaceful cat",
        "Authorization": "Bearer " + GITHUB_TOKEN,
    })

    url_parsed = urllib.parse.urlparse(url)
    body = body and json.dumps(body)
    conn = http.client.HTTPSConnection(url_parsed.hostname)
    conn.request(method, url_parsed.path, body=body, headers=headers)
    response = conn.getresponse()
    if response.status == 301:
        return github_request(method, response.headers["Location"])
    return response


def get_key(key, jeez, error=True):
    """Get key as a string like foo.bar.blah in dict => [foo][bar][blah] """
    curr = jeez
    for k in key.split("."):
        if k not in curr:
            if error:
                raise CouldNotFindConfigKeyException(
                    f"Could not find key {key} in json while parsing file")
            return ""
        curr = curr[k]
    if not isinstance(curr, str):
        curr = str(curr)
    return curr


def get_errors(text):
    """ Get all errors coming from """
    errorstrings = r"(error|fail(ed)?)"
    errorre = re.compile("^(.*%s.*)$" % (errorstrings),
                         re.IGNORECASE | re.MULTILINE)
    ret = ''
    for i in errorre.findall(text):
        i = re.sub(errorstrings, r"**\1**", i[0], flags=re.IGNORECASE)
        ret += f" * <code>{i}</code>\n"

    if not ret:
        return ""
    return f"""
Errors detected :

{ret}

"""


def kapply(yaml_file, jeez, parameters_extras, namespace, name=None):
    """Apply kubernetes yaml template in a namespace with simple transformations
    from a dict"""
    def tpl_apply(param):
        if param in parameters_extras:
            return parameters_extras[param]

        if get_key(param, jeez, error=False):
            return get_key(param, jeez)

        return '{{%s}}' % (param)

    if not name:
        name = yaml_file
    print(f"Processing {name} in {namespace}")
    tmpfile = tempfile.NamedTemporaryFile(delete=False).name
    open(tmpfile, 'w').write(
        re.sub(r"\{\{([_a-zA-Z0-9\.]*)\}\}", lambda m: tpl_apply(m.group(1)),
               open(yaml_file).read()))
    execute(
        f"kubectl apply -f {tmpfile} -n {namespace}",
        "Cannot apply {tmpfile} in {namespace} with {string(transformations)}")
    os.remove(tmpfile)


def main():
    """main function"""
    # This will get better when we rewrite all of this with objects and such...
    # wildly using global like when I was a teenager back in the 80s writting
    # locomotive basic
    # pylint: disable=global-statement
    global CHECK_RUN_ID, REPO_FULL_NAME
    checked_repo = "/tmp/repository"

    # # Testing
    # jeez = json.load(
    #     open(os.path.expanduser("~/tmp/tekton/apply-change-of-a-task/t.json")))
    param = """$(params.github_json)""".replace(
        "\n", " ")  # TODO: why is it that json lib bugs on newline
    if not param:
        print("Cannot find a github_json param")
        sys.exit(1)
    jeez = json.loads(param)
    random_str = ''.join(
        random.choices(string.ascii_letters + string.digits, k=4)).lower()
    pull_request_sha = get_key('pull_request.head.sha', jeez)
    pull_request_number = get_key('pull_request.number', jeez)
    REPO_FULL_NAME = get_key('repository.full_name', jeez)
    repo_owner_login = get_key('repository.owner.login', jeez)
    repo_html_url = get_key('repository.html_url', jeez)

    # Extras template parameters to add aside of the stuff from json
    parameters_extras = {
        'revision': pull_request_sha,
        'repo_url': repo_html_url,
        'repo_owner': repo_owner_login,
    }

    namespace = f"pull-{pull_request_sha[:4]}-{random_str}"

    target_url = ""
    openshift_console_url = execute(
        "kubectl get route -n openshift-console console -o jsonpath='{.spec.host}'",
        check_error="cannot openshift-console route")

    if openshift_console_url.returncode == 0:
        target_url = f"https://{openshift_console_url.stdout.decode()}/k8s/ns/{namespace}/tekton.dev~v1beta1~PipelineRun/"

    # Set status as pending
    check_run_json = github_request(
        "POST",
        # Not posting the pull request full_name which is the fork but where the
        # pr happen.
        f"https://{GITHUB_HOST_URL}/repos/{REPO_FULL_NAME}/check-runs",
        headers={
            "Accept": "application/vnd.github.antiope-preview+json"
        },
        body={
            "name": 'tekton-asa-code',
            "details_url": target_url,
            "status": "in_progress",
            'head_sha': pull_request_sha,
            'started_at':
            datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }).read().decode()
    check_run_json = json.loads(check_run_json)
    CHECK_RUN_ID = check_run_json['id']

    if not os.path.exists(checked_repo):
        os.makedirs(checked_repo)
        os.chdir(checked_repo)

        exec_init = execute("git init")
        if exec_init.returncode != 0:
            print("Error creating a GitHUB repo in {checked_repo}")
            print(exec_init.stdout.decode())
            print(exec_init.stderr.decode())

    os.chdir(checked_repo)

    cmds = [
        f"git remote add origin https://{repo_owner_login}:{GITHUB_TOKEN}@{repo_html_url.replace('https://', '')}",
        f"git fetch origin refs/pull/{pull_request_number}/head",
        f"git reset --hard {pull_request_sha}"
    ]
    for cmd in cmds:
        execute(
            cmd, "Error checking out the GitHUB repo %s to the branch %s" %
            (repo_html_url, pull_request_sha))

    # Exit if there is not tekton directory
    if not os.path.exists("./tekton"):
        # Set status as pending
        output = github_check_set_status(
            get_key('repository.full_name', jeez),
            CHECK_RUN_ID,
            "https://tenor.com/search/sad-cat-gifs",
            conclusion="neutral",
            output={
                "title": "Tekton as a code",
                "summary": "Skipping this check 🤷🏻‍♀️",
                "text": "No tekton directoy has been found 😿"
            })
        print("No tekton directoy has been found 😿")
        sys.exit(0)

    execute(f"kubectl create ns {namespace}",
            "Cannot create a temporary namespace")
    print(f"Namespace {namespace} has been created")

    # Apply label!
    execute(
        f'kubectl label namespace {namespace} generated-by="tekton-asa-code"')

    if os.path.exists(f"{checked_repo}/tekton/install.map"):
        print(f"Processing install.map: {checked_repo}/tekton/install.map")
        for line in open(f"{checked_repo}/tekton/install.map"):
            line = line.strip()
            if not line:
                continue

            if line.startswith("#"):
                continue

            # remove inline comments
            if " #" in line:
                line = line[:line.find(" #")]

            # if we have something like catalog:// do some magic :
            # in: catalog://official:git-clone:0.1
            # out: https://raw.githubusercontent.com/tektoncd/catalog/master/task/git-clone/0.1/git-clone.yaml
            if line.startswith("catalog://"):
                splitted = line.replace("catalog://", "").split(":")
                if len(splitted) != 3:
                    print(f'The line in install.map:"{line}" is invalid')
                    continue
                if splitted[0] not in CATALOGS:
                    print(
                        f'The catalog "{splitted[0]}" in line: "{line}" is invalid'
                    )
                    continue

                line = f"{CATALOGS[splitted[0]]}/{splitted[1]}/{splitted[2]}/{splitted[1]}.yaml"

            # if we have a URL retrieve it (with GH token)
            if line.startswith("https://"):
                try:
                    url_retrieved, _ = urllib.request.urlretrieve(line)
                except urllib.error.HTTPError as http_error:
                    msg = f"Cannot retrieve remote task {line} as specified in install.map: {http_error}"
                    print(msg)
                    github_check_set_status(REPO_FULL_NAME,
                                            check_run_json['id'],
                                            "",
                                            conclusion="failure",
                                            output={
                                                "title": "Tekton as a code",
                                                "summary":
                                                "Cannot find remote task 💣",
                                                "text": msg,
                                            })
                    sys.exit(1)
                kapply(url_retrieved,
                       jeez,
                       parameters_extras,
                       namespace,
                       name=line)
            elif os.path.exists(f"{checked_repo}/tekton/{line}"):
                kapply(f"{checked_repo}/tekton/{line}", jeez,
                       parameters_extras, namespace)
            else:
                print(
                    f"The file {line} specified in install.map is not found in tekton repository"
                )
    else:
        for filename in os.listdir(os.path.join(checked_repo, "tekton")):
            if not filename.endswith(".yaml"):
                continue
            kapply(filename, jeez, parameters_extras, namespace)

    time.sleep(2)

    output_file = tempfile.NamedTemporaryFile(delete=False).name
    stream(f"tkn pr logs -n {namespace} --follow --last", output_file,
           f"Cannot show Pipelinerun log in {namespace}")
    output = open(output_file).read()

    # TODO: Need a better way!
    describe_output = execute(
        f"tkn pr describe -n {namespace} --last").stdout.decode()
    regexp = re.compile(r"^STARTED\s*DURATION\s*STATUS\n(.*)$", re.MULTILINE)
    status = regexp.findall(describe_output)[0].split(" ")[-1]
    status_emoji = "💥" if 'Failed' in status else '👌'

    print(describe_output)

    pipelinerun_output = ""
    if output:
        pipelinerun_output = f"""<details>
<summary>PipelineRun Output</summary>

```
{output}
```
</details>

"""

    # Set status as pending
    github_check_set_status(
        REPO_FULL_NAME,
        check_run_json['id'],
        # Only set target_url which goest to the namespace in case of failure,
        # since we delete the namespace in case of success.
        (status.lower() == 'failed' and target_url or ''),
        (status.lower() == 'failed' and 'failure' or 'success'),
        {
            "title":
            "Tekton has code report",
            "summary":
            f"CI has **{status}** {status_emoji}",
            "text":
            f"""

{get_errors(output)}
{pipelinerun_output}
<details>
<summary>PipelineRun status</summary>

```
{describe_output}
```
</details>

"""
        })
    if status == "Failed":
        sys.exit(1)

    # Only delete if it succeed, keeping it for investigation
    execute(f"kubectl delete ns {namespace}",
            "Cannot delete temporary namespace {namespace}")


if __name__ == '__main__':
    try:
        main()
    except Exception:
        exc_type, exc_value, exc_tb = sys.exc_info()
        tb = traceback.format_exception(exc_type, exc_value, exc_tb)
        print(" ".join(tb))
        if CHECK_RUN_ID:
            github_check_set_status(REPO_FULL_NAME,
                                    CHECK_RUN_ID,
                                    "https://tenor.com/search/sad-cat-gifs",
                                    conclusion="failure",
                                    output={
                                        "title": "Tekton as a code",
                                        "summary":
                                        "Tekton asa code has failed 💣",
                                        "text": " ".join(tb)
                                    })
        sys.exit(1)
