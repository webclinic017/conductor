# Copyright 2021 Foundries.io
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import requests
import subprocess
from conductor.celery import app as celery
from celery.utils.log import get_task_logger
from conductor.core.models import Run, Build, LAVADeviceType, LAVAJob, Project
from django.conf import settings
from django.db import transaction
from django.template.loader import get_template
from urllib.parse import urljoin

logger = get_task_logger(__name__)


def _get_os_tree_hash(url, project):
    logger.debug("Retrieving ostree hash with base url: %s" % url)
    # ToDo: add headers for authentication
    token = getattr(settings, "FIO_API_TOKEN", None)
    authentication = {
        "OSF-TOKEN": token,
    }
    os_tree_hash_request = requests.get(urljoin(url, "other/ostree.sha.txt"), headers=authentication)
    if os_tree_hash_request.status_code == 200:
        return os_tree_hash_request.text.strip()
    return None


@celery.task
def create_build_run(build_id, run_url, run_name):
    logger.debug("Received task for build: %s" % build_id)
    device_type = None
    try:
        device_type = LAVADeviceType.objects.get(name=run_name)
    except LAVADeviceType.DoesNotExist:
        return None
    build = None
    try:
        build = Build.objects.get(pk=build_id)
    except Build.DoesNotExist:
        return None
    # compose LAVA job definitions for each device
    run, _ = Run.objects.get_or_create(
        build=build,
        device_type=device_type,
        ostree_hash=_get_os_tree_hash(run_url, build.project),
        run_name=run_name
    )
    context = {
        "device_type": run_name,
        "build_url": build.url,
        "build_id": build.build_id,

        "IMAGE_URL": "%slmp-factory-image-%s.wic.gz" % (run_url, run_name),
        "BOOTLOADER_URL": "%simx-boot-%s" % (run_url, run_name),
        "SPLIMG_URL": "%sSPL-%s" % (run_url, run_name),
        "prompts": ["fio@%s" % run_name, "Password:", "root@%s" % run_name],
        "net_interface": device_type.net_interface,
        "os_tree_hash": run.ostree_hash,
        "target": build.build_id,
    }
    dt_settings = device_type.get_settings()
    for key, value in dt_settings.items():
        try:
            context.update({key: value.format(run_url=run_url, run_name=run_name)})
        except KeyError:
            # ignore KeyError in case of misformatted string
            pass
        except AttributeError:
            # ignore values that are not strings
            pass
    template = get_template("lava_template.yaml")
    lava_job_definition = template.render(context)
    job_ids = build.project.submit_lava_job(lava_job_definition)
    logger.debug(job_ids)
    for job in job_ids:
        LAVAJob.objects.create(
            job_id=job,
            definition=lava_job_definition,
            project=build.project
        )


@celery.task
def update_build_commit_id(build_id, run_url):
    token = getattr(settings, "FIO_API_TOKEN", None)
    authentication = {
        "OSF-TOKEN": token,
    }
    run_json_request = requests.get(urljoin(run_url, ".rundef.json"), headers=authentication)
    if run_json_request.status_code == 200:
        with transaction.atomic():
            build = None
            try:
                build = Build.objects.get(pk=build_id)
            except Build.DoesNotExist:
                return None

            run_json = run_json_request.json()
            commit_id = run_json['env']['GIT_SHA']
            build.commit_id = commit_id
            build.save()


class ProjectMisconfiguredError(Exception):
    pass


def __project_repository_exists(project):
    repository_path = os.path.join(settings.FIO_REPOSITORY_HOME, project.name)
    if os.path.exists(repository_path):
        if os.path.isdir(repository_path):
            # do nothing, directory exists
            return True
        else:
            # raise exception, there should not be a file with this name
            raise ProjectMisconfiguredError()
    return False


@celery.task
def create_project_repository(project_id):
    project = None
    try:
        project = Project.objects.get(pk=project_id)
    except Project.DoesNotExist:
        # do nothing if project is not found
        return
    # check if repository DIR already exists
    repository_path = os.path.join(settings.FIO_REPOSITORY_HOME, project.name)
    if not __project_repository_exists(project):
        # create repository DIR
        os.makedirs(repository_path)
    # call shell script to clone and configure repository
    cmd = [os.path.join(settings.FIO_REPOSITORY_SCRIPT_PATH_PREFIX, "checkout_repository.sh"),
           "-d", repository_path,
           "-r", settings.FIO_REPOSITORY_REMOTE_NAME,
           "-u", "%s/%s/lmp-manifest.git" % (settings.FIO_REPOSITORY_BASE, project.name),
           "-l", settings.FIO_BASE_REMOTE_NAME,
           "-w", settings.FIO_BASE_MANIFEST,
           "-t", settings.FIO_REPOSITORY_TOKEN]
    print(cmd)
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        pass


@celery.task
def merge_lmp_manifest():
    # merge LmP manifest into all project manifst repositories
    projects = Project.objects.all()
    for project in projects:
        repository_path = os.path.join(settings.FIO_REPOSITORY_HOME, project.name)
        if not __project_repository_exists(project):
            # ignore project with no repository
            continue
        # call shell script to merge manifests
        cmd = [os.path.join(settings.FIO_REPOSITORY_SCRIPT_PATH_PREFIX,"merge_manifest.sh"),
               "-d", repository_path,
               "-r", settings.FIO_REPOSITORY_REMOTE_NAME,
               "-l", settings.FIO_BASE_REMOTE_NAME]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            pass


#@celery.task
#def check_build_release():
#    # this task should run once or twice a day
#    # it fills in Build.is_release field based on the repository tags
#    pass
