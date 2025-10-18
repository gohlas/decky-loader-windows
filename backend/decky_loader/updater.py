import asyncio
import logging
import os
import subprocess
import shutil
import aiohttp

from helpers import service_restart, get_keep_systemd_service
from constants import ON_LINUX, ON_WINDOWS

logger = logging.getLogger(__name__)

class Updater:
    def __init__(self, context):
        self.context = context
        self.remoteVer = None

        context.ws.add_route("updater/do_update", self.do_update)

    # --- Your custom update path for Windows ---
    async def run_custom_windows_update(self):
        """Notify user, wait 5 seconds, then run update_server.bat."""
        if not ON_WINDOWS:
            logger.info("Custom Windows updater called on non-Windows system.")
            return

        # The .bat file you want to run
        bat_path = os.path.join(os.getcwd(), "update_server.bat")

        if not os.path.exists(bat_path):
            logger.error(f"update_server.bat not found at {bat_path}")
            try:
                await self.context.ws.emit("updater/message", "Update script not found.")
            except Exception:
                pass
            return

        message = "Decky Loader will shut down in 5 seconds to update..."
        logger.info(message)

        # Send message to frontend
        try:
            await self.context.ws.emit("updater/message", message)
        except Exception as e:
            logger.warning(f"Could not emit WebSocket message: {e}")

        # Wait 5 seconds before executing
        await asyncio.sleep(5)

        logger.info(f"Executing update script: {bat_path}")
        try:
            # Run the .bat file without blocking
            subprocess.Popen(
                ["cmd.exe", "/c", bat_path],
                cwd=os.getcwd(),
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        except Exception as e:
            logger.error(f"Failed to execute update script: {e}")
            return

        logger.info("Shutting down Decky Loader...")
        os._exit(0)

    # --- The main update method ---
    async def do_update(self):
        """Handles the update process."""
        if ON_WINDOWS:
            # Run your custom .bat updater on Windows
            await self.run_custom_windows_update()
            return

        # --- Normal Decky update flow for Linux ---
        if not self.remoteVer:
            logger.error("No remote version info available. Cannot update.")
            return

        version = self.remoteVer["tag_name"]
        download_filename = "PluginLoader"

        for x in self.remoteVer["assets"]:
            if x["name"] == download_filename:
                download_url = x["browser_download_url"]
                size_in_bytes = x["size"]
                break
        else:
            logger.error("Could not find valid Decky binary in release assets.")
            return

        # Download and install the Linux binary
        await self.download_decky_binary(download_url, version, size_in_bytes)
        await self.do_restart()

    # --- The regular download method (Linux only) ---
    async def download_decky_binary(self, download_url: str, version: str, size_in_bytes: int = 0):
        """Downloads and replaces Decky binary (Linux only)."""
        logger.info(f"Downloading Decky Loader version {version} from {download_url}")
        new_binary_path = "PluginLoader.new"
        old_binary_path = "PluginLoader"

        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as res:
                res.raise_for_status()
                total = size_in_bytes or int(res.headers.get("Content-Length", 0))
                raw = 0
                with open(new_binary_path, "wb") as out:
                    async for chunk in res.content.iter_chunked(512):
                        out.write(chunk)
                        raw += len(chunk)
                        if total > 0:
                            progress = (raw / total) * 100
                            await self.context.ws.emit("updater/update_download_percentage", progress)

        shutil.move(new_binary_path, old_binary_path)
        os.chmod(old_binary_path, 0o755)
        logger.info("Decky binary updated successfully.")
        await self.context.ws.emit("updater/finish_download")

    async def do_restart(self):
        """Restarts the plugin loader service after update."""
        logger.info("Restarting Decky Loader service...")
        await service_restart("plugin_loader", block=False)