"""AsyncIO client for communicating with Jupyter.

Allows the caller to login to the hub, spawn lab
containers, and then run jupyter kernels remotely."""

__all__ = [
    "JupyterClient",
]

import asyncio
import random
import string
from dataclasses import dataclass
from http.cookies import BaseCookie
from uuid import uuid4

from aiohttp import ClientSession
from structlog._config import BoundLoggerLazyProxy

from mobu.config import Configuration
from mobu.user import User


@dataclass
class JupyterClient:
    log: BoundLoggerLazyProxy
    user: User
    session: ClientSession
    headers: dict
    xsrftoken: str
    jupyter_url: str

    def __init__(self, user: User, log: BoundLoggerLazyProxy):
        self.user = user
        self.log = log
        self.jupyter_url = Configuration.environment_url + "/nb/"
        self.xsrftoken = "".join(
            random.choices(string.ascii_uppercase + string.digits, k=16)
        )

        self.headers = {
            "Authorization": "Bearer " + user.token,
            "x-xsrftoken": self.xsrftoken,
        }

        self.session = ClientSession(headers=self.headers)
        self.session.cookie_jar.update_cookies(
            BaseCookie({"_xsrf": self.xsrftoken})
        )

    async def hub_login(self) -> None:
        async with self.session.get(self.jupyter_url + "hub/login") as r:
            if r.status != 200:
                raise Exception(f"Error {r.status} from {r.url}")

            home_url = self.jupyter_url + "hub/home"
            if str(r.url) != home_url:
                raise Exception(
                    f"Redirected to {r.url} but expected {home_url}"
                )

    async def ensure_lab(self) -> None:
        self.log.info("Ensure lab")
        running = await self.is_lab_running()
        if running:
            await self.lab_login()
        else:
            await self.spawn_lab()

    async def lab_login(self) -> None:
        self.log.info("Logging into lab")
        lab_url = self.jupyter_url + f"user/{self.user.username}/lab"
        async with self.session.get(lab_url) as r:
            if r.status != 200:
                raise Exception(f"Error {r.status} from {r.url}")

    async def is_lab_running(self) -> bool:
        self.log.info("Is lab running?")
        hub_url = self.jupyter_url + "hub"
        async with self.session.get(hub_url) as r:
            if r.status != 200:
                self.log.error(f"Error {r.status} from {r.url}")

            spawn_url = self.jupyter_url + "hub/spawn"
            self.log.info(f"Going to {hub_url} redirected to {r.url}")
            if str(r.url) == spawn_url:
                return False

        return True

    async def spawn_lab(self) -> None:
        body = {
            "kernel_image": "lsstsqre/sciplat-lab:recommended",
            "image_tag": "latest",
            "size": "small",
        }

        spawn_url = self.jupyter_url + "hub/spawn"
        lab_url = self.jupyter_url + f"user/{self.user.username}/lab"

        # DM-23864: Do a get on the spawn URL even if I don't have to.
        async with self.session.get(spawn_url) as r:
            await r.text()

        async with self.session.post(
            spawn_url, data=body, allow_redirects=False
        ) as r:
            if r.status != 302:
                raise Exception(f"Error {r.status} from {r.url}")

            progress_url = r.url
            self.log.info(f"Watching progress url {progress_url}")

        while True:
            async with self.session.get(progress_url) as r:
                if str(r.url) == lab_url:
                    self.log.info(f"Lab spawned, redirected to {r.url}")
                    return
                else:
                    self.log.info(f"Still waiting for lab to spawn {r}")
                    await asyncio.sleep(15)

    async def delete_lab(self) -> None:
        headers = {"Referer": self.jupyter_url + "hub/home"}

        server_url = (
            self.jupyter_url + f"hub/api/users/{self.user.username}/server"
        )
        self.log.info(f"Deleting lab for {self.user.username} at {server_url}")

        async with self.session.delete(server_url, headers=headers) as r:
            if r.status not in [200, 202, 204]:
                raise Exception(f"Error {r.status} from {r.url}")

    async def create_kernel(self, kernel_name: str = "python") -> str:
        kernel_url = (
            self.jupyter_url + f"user/{self.user.username}/api/kernels"
        )
        body = {"name": kernel_name}

        async with self.session.post(kernel_url, json=body) as r:
            if r.status != 201:
                raise Exception(f"Error {r.status} from {r.url}")

            response = await r.json()
            return response["id"]

    async def run_python(self, kernel_id: str, code: str) -> str:
        kernel_url = (
            self.jupyter_url
            + f"user/{self.user.username}/api/kernels/{kernel_id}/channels"
        )

        msg_id = uuid4().hex

        msg = {
            "header": {
                "username": "",
                "version": "5.0",
                "session": "",
                "msg_id": msg_id,
                "msg_type": "execute_request",
            },
            "parent_header": {},
            "channel": "shell",
            "content": {
                "code": code,
                "silent": False,
                "store_history": False,
                "user_expressions": {},
                "allow_stdin": False,
            },
            "metadata": {},
            "buffers": {},
        }

        async with self.session.ws_connect(kernel_url) as ws:
            await ws.send_json(msg)

            while True:
                r = await ws.receive_json()
                msg_type = r["msg_type"]
                if msg_type == "error":
                    raise Exception(f"Error running python {r}")
                elif (
                    msg_type == "stream"
                    and msg_id == r["parent_header"]["msg_id"]
                ):
                    return r["content"]["text"]

    def dump(self) -> dict:
        return {
            "cookies": [str(cookie) for cookie in self.session.cookie_jar],
        }
