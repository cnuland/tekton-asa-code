#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: Chmouel Boudjnah <chmouel@chmouel.com>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
"""Script to send a slack notification to be plugged in a finally task"""
import argparse
import json
import os
import subprocess
import sys
import typing
import urllib.request


class SlackNotificationError(Exception):
    """Custom exception when we fail"""
    pass


def check_label(label_eval: str, label_to_check: str) -> bool:
    """Check a label: if you get a string that has all the labels as specified
    by github, it will eval it and check if one contains the label_to_check"""
    return bool([x for x in eval(label_eval) if x['name'] == label_to_check])  # pylint: disable=eval-used


def check_status_of_pipelinerun(pipelinerun: str) -> typing.List[str]:
    """Check status of a pipelinerun using kubectl, we avoid the the Running
    ones since we run in finally, it will have a running ones"""
    cmd = f"kubectl get pipelinerun {pipelinerun} -o json"
    ret = subprocess.run(cmd, shell=True, check=True, capture_output=True)
    if ret.returncode != 0:
        raise SlackNotificationError(f"Could not run command: {cmd}")
    jeez = json.loads(ret.stdout)
    task_runs = jeez['status']['taskRuns']
    failed = []

    pname = jeez['metadata']['name']
    for task in task_runs.keys():
        bname = task.replace(pname + "-", '')
        bname = bname.replace("-" + bname.split("-")[-1], '')
        if bool([
                x['message'] for x in task_runs[task]['status']['conditions']
                if x['status'] != 'Running' and x['status'] == 'False'
        ]):
            failed.append(bname)
    return failed


def send_slack_message(webhook_url: str, subject: str, text: str,
                       icon: str) -> str:
    """Send a slack message"""
    msg = {
        "text":
        subject,
        "attachments": [{
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": text,
                    },
                    "accessory": {
                        "type": "image",
                        "image_url": icon,
                        "alt_text": "From tekton with love"
                    }
                },
            ]
        }]
    }

    req = urllib.request.Request(webhook_url,
                                 data=json.dumps(msg).encode(),
                                 headers={"Content-type": "application/json"},
                                 method="POST")
    # TODO: Handle error?
    return urllib.request.urlopen(req).read().decode()


def main() -> int:
    """Main"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--label-to-check", help="Label to check")
    parser.add_argument(
        "--failure-url-icon",
        default="https://publicdomainvectors.org/photos/21826-REMIX-ARRET.png",
        help="The icon of failure")
    parser.add_argument(
        "--success-url-icon",
        default="https://publicdomainvectors.org/photos/Checkmark.png",
        help="The icon of success")

    parser.add_argument("--failure-subject",
                        help="The subject of the slack message when failure",
                        default="CI has failed :cry:")
    parser.add_argument("--success-subject",
                        help="The subject of the slack message when succes",
                        default="CI has succeeded :thumbsup:")
    parser.add_argument("--log-url", help="Link to the log url")

    parser.add_argument(
        "--github-pull-label",
        required=True,
        help="pull_request.labels dict as get from tekton asa code")

    parser.add_argument("--pipelinerun",
                        default=os.environ.get("PIPELINERUN"),
                        help="The pipelinerun to check the status on")

    parser.add_argument("--slack-webhook-url",
                        default=os.environ.get("SLACK_WEBHOOK_URL"),
                        help="Slack webhook URL")

    args = parser.parse_args()
    if args.label_to_check and args.github_pull_label:
        if not check_label(args.github_pull_label, args.label_to_check):
            print(
                f"Pull request doesn't have the label {args.label_to_check} skipping."
            )
            return 0

    if not args.pipelinerun:
        print(
            "error --pipelinerun need to be set via env env variable or other means."
        )
        return 1

    if not args.slack_webhook_url:
        print(
            "error --slack-webhook-url need to be set via env variable or other means."
        )
        return 1

    failures = check_status_of_pipelinerun(args.pipelinerun)
    if failures:
        slack_icon = args.failure_url_icon
        slack_subject = args.failure_subject
        slack_text = f"""• *Failed Tasks*: {", ".join(failures)}\n"""
    else:
        slack_icon = args.success_url_icon
        slack_subject = args.success_subject
        slack_text = "\n"

    if args.log_url:
        slack_text += f"• *PipelineRun logs*: {args.log_url}"

    ret = send_slack_message(args.slack_webhook_url, slack_subject, slack_text,
                             slack_icon)
    if ret:
        print(ret)

    return 0


if __name__ == '__main__':
    sys.exit(main())