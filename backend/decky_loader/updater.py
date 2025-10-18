from __future__ import annotations
import os
import asyncio
import subprocess
from aiohttp import ClientSession
from logging import getLogger
from asyncio import sleep
from typing import TYPE_CHECKING, List, TypedDict

if TYPE_CHECKING:
    from .main import PluginManager

from . import helpers

logger = getLogger("Updater")


class RemoteVerAsset(TypedDict):
    name: str
    size: int
    browser_download_url: str


class RemoteVer(TypedDict):
    tag_name: str
    prerelease: bool
    assets: List[RemoteVerAsset]


class Updater:
    def __init__(self, context: PluginManager) -> None:
        self.context = context
        self.remoteVer: RemoteVer | None = None
        self.allRemoteVers: List[RemoteVer] = []
        self.localVer = helpers.get_loader_version()

        # WebSocket routes
        context.ws.add_route("updater/check_for_updates", self.check_for_updates)
        context.ws.add_route("updater/do_update", self.do_update)
        context.loop.create_task(self.version_reloader())

    async def check_for_updates(self):
        """
        Check the official Decky Loader GitHub repo for both release and prerelease versions.
        """
        logger.debug("Checking for updates from Decky Loader GitHub...")
        async with ClientSession() as web:
            # Get the most recent releases (including prereleases)
            async with web.request(
                "GET",
                "https://api.github.com/repos/SteamDeckHomebrew/decky-loader/releases",
                headers={"X-GitHub-Api-Version": "2022-11-28"},
                ssl=helpers.get_ssl_context(),
            ) as res:
                releases: List[RemoteVer] = await res.json()

        # Keep both prereleases and releases
        self.allRemoteVers = releases
        self.remoteVer = releases[0] if releases else None

        logger.info(f"Fetched remote Decky Loader version info: {self.remoteVer.get('tag_name', 'unknown') if self.remoteVer else 'None'}")

        await self.context.ws.emit("loader/notify_updates")

        return {
            "current": self.localVer,
            "remote": self.remoteVer,
            "updatable": self.localVer != "unknown" and self.localVer != "dev",
        }

    async def version_reloader(self):
        """Auto-check for updates every 6 hours."""
        await sleep(30)
        while True:
            try:
                await self.check_for_updates()
            except Exception:
                logger.exception("Version reloader failed.")
            await sleep(60 * 60 * 6)

    async def do_update(self):
        """Trigger update.bat after short delay."""
        logger.info("Starting update using update.bat...")
        await self.run_bat_update()

    async def run_bat_update(self):
        """Runs update.bat to perform the update and exits Decky Loader."""
        message = "Decky Loader will shut down in 5 seconds to update..."
        logger.info(message)
        try:
            await self.context.ws.emit("updater/message", message)
        except Exception:
            pass

        await asyncio.sleep(5)

        bat_path = os.path.join(os.getcwd(), "update.bat")
        if not os.path.exists(bat_path):
            logger.error(f"Update script not found: {bat_path}")
            return

        subprocess.Popen(
            ["cmd.exe", "/c", bat_path],
            cwd=os.getcwd(),
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        logger.info("Update script launched. Exiting Decky Loader.")
        os._exit(0)