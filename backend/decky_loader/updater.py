from __future__ import annotations
from asyncio import sleep
from logging import getLogger
import os
from os import getcwd, path, remove
from typing import TYPE_CHECKING, List, TypedDict
if TYPE_CHECKING:
    from .main import PluginManager
from .localplatform.localplatform import chmod, service_restart, service_stop, ON_LINUX, ON_WINDOWS, get_keep_systemd_service, get_selinux
import shutil
import zipfile

from aiohttp import ClientSession

from . import helpers
from .settings import SettingsManager
if TYPE_CHECKING:
    from .main import PluginManager


logger = getLogger("Updater")


class RemoteVerAsset(TypedDict):
    name: str
    size: int
    browser_download_url: str


class RemoteVer(TypedDict):
    tag_name: str
    prerelease: bool
    assets: List[RemoteVerAsset]


class TestingVersion(TypedDict):
    id: int
    name: str
    link: str
    head_sha: str


class Updater:
    def __init__(self, context: PluginManager) -> None:
        self.context = context
        self.settings = self.context.settings
        self.remoteVer: RemoteVer | None = None
        self.allRemoteVers: List[RemoteVer] = []
        self.localVer = helpers.get_loader_version()

        try:
            self.currentBranch = self.get_branch(self.context.settings)
        except:
            self.currentBranch = 0
            logger.error("Current branch could not be determined, defaulting to \"Stable\"")

        if context:
            context.ws.add_route("updater/get_version_info", self.get_version_info)
            context.ws.add_route("updater/check_for_updates", self.check_for_updates)
            context.ws.add_route("updater/do_restart", self.do_restart)
            context.ws.add_route("updater/do_shutdown", self.do_shutdown)
            context.ws.add_route("updater/do_update", self.do_update)
            context.ws.add_route("updater/get_testing_versions", self.get_testing_versions)
            context.ws.add_route("updater/download_testing_version", self.download_testing_version)
            context.loop.create_task(self.version_reloader())

    def get_branch(self, manager: SettingsManager):
        ver = manager.getSetting("branch", -1)
        logger.debug("current branch: %i" % ver)
        if ver == -1:
            logger.info("Current branch is not set, determining branch from version...")
            if self.localVer.startswith("v") and "-pre" in self.localVer:
                logger.info("Current version determined to be pre-release")
                manager.setSetting('branch', 1)
                return 1
            else:
                logger.info("Current version determined to be stable")
                manager.setSetting('branch', 0)
                return 0
        return ver

    async def _get_branch(self, manager: SettingsManager):
        return self.get_branch(manager)

    def get_service_url(self):
        logger.debug("Getting service URL")
        branch = self.get_branch(self.context.settings)
        match branch:
            case 0:
                url = "https://raw.githubusercontent.com/SteamDeckHomebrew/decky-loader/main/dist/plugin_loader-release.service"
            case 1 | 2:
                url = "https://raw.githubusercontent.com/SteamDeckHomebrew/decky-loader/main/dist/plugin_loader-prerelease.service"
            case _:
                logger.error("Invalid branch set; defaulting to prerelease service.")
                url = "https://raw.githubusercontent.com/SteamDeckHomebrew/decky-loader/main/dist/plugin_loader-prerelease.service"
        return str(url)

    async def get_version_info(self):
        return {
            "current": self.localVer,
            "remote": self.remoteVer,
            "all": self.allRemoteVers,
            "updatable": self.localVer != "unknown" and self.localVer != "dev"
        }

    async def check_for_updates(self):
        logger.debug("checking for updates")
        selectedBranch = self.get_branch(self.context.settings)
        async with ClientSession() as web:
            async with web.request("GET", "https://api.github.com/repos/SteamDeckHomebrew/decky-loader/releases",
                                   headers={'X-GitHub-Api-Version': '2022-11-28'},
                                   ssl=helpers.get_ssl_context()) as res:
                remoteVersions: List[RemoteVer] = await res.json()
                if selectedBranch == 0:
                    remoteVersions = list(filter(lambda ver: ver["tag_name"].startswith("v") and not ver["prerelease"]
                                                              and not ver["tag_name"].find("-pre") > 0, remoteVersions))
                elif selectedBranch == 1:
                    remoteVersions = list(filter(lambda ver: ver["tag_name"].startswith("v"), remoteVersions))
                else:
                    raise ValueError("no valid branch found")
        self.allRemoteVers = remoteVersions
        if selectedBranch == 0:
            self.remoteVer = next(filter(lambda ver: ver["tag_name"].startswith("v") and not ver["prerelease"]
                                                     and not ver["tag_name"].find("-pre") > 0, remoteVersions), None)
        elif selectedBranch == 1:
            self.remoteVer = next(filter(lambda ver: ver["tag_name"].startswith("v"), remoteVersions), None)
        else:
            raise ValueError("no valid branch found")
        logger.info("Updated remote version information")
        await self.context.ws.emit("loader/notify_updates")
        return await self.get_version_info()

    async def version_reloader(self):
        await sleep(30)
        while True:
            try:
                await self.check_for_updates()
            except Exception:
                pass
            await sleep(60 * 60 * 6)  # every 6 hours

    async def download_decky_binary(self, download_url: str, version: str, is_zip: bool = False, size_in_bytes: int | None = None):
        download_filename = "PluginLoader" if ON_LINUX else "PluginLoader.exe"
        download_temp_filename = download_filename + ".new"

        if size_in_bytes is None:
            size_in_bytes = 26214400  # 25MiB

        async with ClientSession() as web:
            logger.debug("Downloading binary")
            async with web.request("GET", download_url, ssl=helpers.get_ssl_context(), allow_redirects=True) as res:
                total = int(res.headers.get('content-length', size_in_bytes))
                if total == 0:
                    total = 1
                with open(path.join(getcwd(), download_temp_filename), "wb") as out:
                    progress = 0
                    raw = 0
                    async for c in res.content.iter_chunked(512):
                        out.write(c)
                        raw += len(c)
                        new_progress = round((raw / total) * 100)
                        if progress != new_progress:
                            self.context.loop.create_task(self.context.ws.emit("updater/update_download_percentage", new_progress))
                            progress = new_progress

        with open(path.join(getcwd(), ".loader.version"), "w", encoding="utf-8") as out:
            out.write(version)

        if ON_LINUX:
            remove(path.join(getcwd(), download_filename))
            if is_zip:
                with zipfile.ZipFile(path.join(getcwd(), download_temp_filename), 'r') as file:
                    file.getinfo(download_filename).filename = download_filename + ".unzipped"
                    file.extract(download_filename)
                remove(path.join(getcwd(), download_temp_filename))
                shutil.move(path.join(getcwd(), download_filename + ".unzipped"), path.join(getcwd(), download_filename))
            else:
                shutil.move(path.join(getcwd(), download_temp_filename), path.join(getcwd(), download_filename))

            chmod(path.join(getcwd(), download_filename), 777, False)
            if get_selinux():
                from asyncio.subprocess import create_subprocess_exec
                process = await create_subprocess_exec("chcon", "-t", "bin_t", path.join(getcwd(), download_filename))
                logger.info(f"Setting the executable flag with chcon returned {await process.wait()}")

        logger.info("Updated loader installation.")
        await self.context.ws.emit("updater/finish_download")
        await self.do_restart()

    async def do_update(self):
        # 🪟 Custom path for Windows updater
        if ON_WINDOWS:
            await self.run_custom_windows_update()
            return

        logger.debug("Starting update.")
        try:
            assert self.remoteVer
        except AssertionError:
            logger.error("Unable to update as remoteVer is missing")
            return

        version = self.remoteVer["tag_name"]
        download_url = None
        size_in_bytes = None
        download_filename = "PluginLoader" if ON_LINUX else "PluginLoader.exe"

        for x in self.remoteVer["assets"]:
            if x["name"] == download_filename:
                download_url = x["browser_download_url"]
                size_in_bytes = x["size"]
                break

        if download_url is None:
            raise Exception("Download url not found")

        service_url = self.get_service_url()
        logger.debug("Retrieved service URL")

        async with ClientSession() as web:
            if ON_LINUX and not get_keep_systemd_service():
                async with web.request("GET", service_url, ssl=helpers.get_ssl_context(), allow_redirects=True) as res:
                    data = await res.content.read()
                service_file_path = path.join(getcwd(), "plugin_loader.service")
                try:
                    with open(service_file_path, "wb") as out:
                        out.write(data)
                except Exception as e:
                    logger.error(f"Error writing service file: {e}")
                with open(service_file_path, "r", encoding="utf-8") as service_file:
                    service_data = service_file.read()
                service_data = service_data.replace("${HOMEBREW_FOLDER}", helpers.get_homebrew_path())
                with open(service_file_path, "w", encoding="utf-8") as service_file:
                    service_file.write(service_data)

                shutil.copy(service_file_path, "/etc/systemd/system/plugin_loader.service")
                if not os.path.exists(path.join(getcwd(), ".systemd")):
                    os.mkdir(path.join(getcwd(), ".systemd"))
                shutil.move(service_file_path, path.join(getcwd(), ".systemd", "plugin_loader.service"))

        await self.download_decky_binary(download_url, version, size_in_bytes=size_in_bytes)

    async def run_custom_windows_update(self):
        """Windows-only updater that runs an external .bat script."""
        import asyncio, subprocess
        message = "Decky Loader will shut down in 5 seconds to update..."
        logger.info(message)
        try:
            await self.context.ws.emit("updater/message", message)
        except Exception:
            pass

        await asyncio.sleep(5)

        bat_path = os.path.join(getcwd(), "update_server.bat")
        if not os.path.exists(bat_path):
            logger.error(f"Update script not found: {bat_path}")
            return

        subprocess.Popen(
            ["cmd.exe", "/c", bat_path],
            cwd=getcwd(),
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        logger.info("External updater launched. Exiting Decky Loader.")
        os._exit(0)

    async def do_restart(self):
        logger.info("Restarting loader for update.")
        await service_restart("plugin_loader", block=False)

    async def do_shutdown(self):
        await service_stop("plugin_loader")

    async def get_testing_versions(self) -> List[TestingVersion]:
        result: List[TestingVersion] = []
        async with ClientSession() as web:
            async with web.request("GET", "https://api.github.com/repos/SteamDeckHomebrew/decky-loader/pulls",
                                   headers={'X-GitHub-Api-Version': '2022-11-28'}, params={'state': 'open'},
                                   ssl=helpers.get_ssl_context()) as res:
                open_prs = await res.json()
                for pr in open_prs:
                    result.append({
                        "id": int(pr['number']),
                        "name": pr['title'],
                        "link": pr['html_url'],
                        "head_sha": pr['head']['sha'],
                    })
        return result

    async def download_testing_version(self, pr_id: int, sha_id: str):
        down_id = ''
        async with ClientSession() as web:
            async with web.request("GET", "https://api.github.com/repos/SteamDeckHomebrew/decky-loader/actions/runs",
                                   headers={'X-GitHub-Api-Version': '2022-11-28'},
                                   params={'head_sha': sha_id}, ssl=helpers.get_ssl_context()) as res:
                works = await res.json()
        for work in works['workflow_runs']:
            if ON_WINDOWS and work['name'] == 'Builder Win':
                down_id = work['id']
                break
            elif ON_LINUX and work['name'] == 'Builder':
                down_id = work['id']
                break
        if down_id != '':
            async with ClientSession() as web:
                async with web.request("GET",
                                       f"https://api.github.com/repos/SteamDeckHomebrew/decky-loader/actions/runs/{down_id}/artifacts",
                                       headers={'X-GitHub-Api-Version': '2022-11-28'},
                                       ssl=helpers.get_ssl_context()) as res:
                    jresp = await res.json()
                    if int(jresp['total_count']) != 0:
                        artifact = jresp['artifacts'][0]
                        down_link = f"https://nightly.link/SteamDeckHomebrew/decky-loader/actions/artifacts/{artifact['id']}.zip"
                        await self.download_decky_binary(
                            down_link,
                            f'PR-{pr_id}',
                            is_zip=True,
                            size_in_bytes=artifact.get('size_in_bytes', None)
                        )
        else:
            logger.error("workflow run not found", str(works))
            raise Exception("Workflow run not found.")