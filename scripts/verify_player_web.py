"""Build an isolated exported game and exercise invitation login and cross-player chat."""
from __future__ import annotations

import io
import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import asyncssh
from playwright.sync_api import expect, sync_playwright

from app.main import make_export, new_world


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(url: str, method="GET", data=None, cookie=""):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read()) if response.headers.get("Content-Type", "").startswith("application/json") else response.read()
            return response.status, response.headers, payload
    except urllib.error.HTTPError as error:
        raise AssertionError(f"{method} {url}: HTTP {error.code}: {error.read().decode(errors='replace')}") from error


def login(base: str, username: str, invitation: str) -> tuple[str, dict]:
    status, headers, result = request(base + "/api/login", "POST", {
        "username": username,
        "password": f"{username} web test password",
        "invitation": invitation,
    })
    assert status == 200, result
    cookie_header = headers.get("Set-Cookie", "")
    assert "Secure" in cookie_header and "HttpOnly" in cookie_header and "SameSite=strict" in cookie_header, cookie_header
    cookie = cookie_header.split(";", 1)[0]
    return cookie, result


def browser_ui_smoke(base: str, invitation: str) -> None:
    executable = os.getenv("MUDMAKER_BROWSER") or shutil.which("google-chrome") or shutil.which("chromium")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base)
        page.locator('input[name="username"]').fill("browserguest")
        page.locator('input[name="password"]').fill("browserguest web test password")
        page.locator('input[name="invitation"]').fill(invitation)
        page.locator('#login-form button').click()
        expect(page.locator("#game")).to_be_visible(timeout=8000)
        expect(page.locator("#connection-state")).to_have_text("Connected")
        page.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller")
        manifest = page.evaluate("fetch('/manifest.webmanifest').then(r=>r.json())")
        assert manifest["display"] == "standalone"
        page.get_by_role("button", name="Start quest: A First Survey").click()
        expect(page.locator("#transcript")).to_contain_text("Quest accepted: A First Survey")
        page.get_by_role("button", name="Talk: Mira the Cartographer").click()
        expect(page.get_by_role("button", name="Choose: I will survey the roads.")).to_be_visible()
        page.get_by_role("button", name="Choose: I will survey the roads.").click()
        expect(page.locator("#transcript")).to_contain_text("three places")

        page.locator("#mode-toggle").click()
        expect(page.locator("body")).to_have_class("terminal-mode")
        expect(page.locator(".actions")).to_be_hidden()
        page.locator("#mode-toggle").click()
        expect(page.locator(".actions")).to_be_visible()

        location_before = page.locator("#location").inner_text()
        page.context.set_offline(True)
        expect(page.locator("#connection-state")).to_contain_text("Offline")
        page.locator("#command").fill("east")
        page.locator("#command-form button").click()
        expect(page.locator("#transcript")).to_contain_text("This command was not sent or saved.")
        page.context.set_offline(False)
        expect(page.locator("#connection-state")).to_have_text("Connected", timeout=8000)
        expect(page.locator("#location")).to_have_text(location_before)
        page.context.set_offline(True)
        page.reload()
        expect(page.locator("#login")).to_be_visible(timeout=5000)
        expect(page.locator("#connection-state")).to_contain_text("Offline", timeout=5000)
        page.context.set_offline(False)
        expect(page.locator("#game")).to_be_visible(timeout=8000)
        expect(page.locator("#location")).to_have_text(location_before)
        assert not errors, errors
        browser.close()


async def ssh_chat(port: int, key: asyncssh.SSHKey, web_base: str, browser_cookie: str) -> None:
    async with asyncssh.connect("127.0.0.1", port=port, username="alice", client_keys=[key], known_hosts=None) as connection:
        process = await connection.create_process()
        await asyncio.wait_for(process.stdout.readuntil("alice > "), timeout=8)
        process.stdin.write("say hello from SSH\n")
        await process.stdin.drain()
        sent = await asyncio.wait_for(process.stdout.readuntil("alice > "), timeout=8)
        assert "alice says: hello from SSH" in sent, sent
        status, _, events = await asyncio.to_thread(request, web_base + "/api/events", cookie=browser_cookie)
        assert status == 200 and any("alice says: hello from SSH" in line for line in events["messages"]), events
        status, _, sent = await asyncio.to_thread(request, web_base + "/api/command", "POST", {"command": "say hello from web"}, browser_cookie)
        assert status == 200 and any("bob says: hello from web" in line for line in sent["messages"]), sent
        from_web = await asyncio.wait_for(process.stdout.readuntil("bob says: hello from web"), timeout=8)
        assert "bob says: hello from web" in from_web, from_web
        connection.close()
        await asyncio.wait_for(connection.wait_closed(), timeout=4)


def main() -> None:
    source = new_world()
    source["runtime_config"]["player_web"] = {"enabled": True, "port": 8080}
    ssh_key = asyncssh.generate_private_key("ssh-ed25519")
    source["ssh_public_keys"] = [{"username": "alice", "public_key": ssh_key.export_public_key().decode().strip()}]
    archive = make_export(source)
    host_ports = {"MUD_SSH_PORT": free_port(), "MUD_MCP_PORT": free_port(), "MUD_WEB_PORT": free_port(), "MUD_WEB_BIND": "127.0.0.1"}
    with tempfile.TemporaryDirectory(prefix="mudmaker-player-web-") as tmp:
        work = Path(tmp)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            zf.extractall(work)
        env = {**os.environ, "COMPOSE_PROJECT_NAME": f"mudmaker-web-{secrets.token_hex(4)}", **{key: str(value) for key, value in host_ports.items()}}

        def compose(*args, timeout=240):
            result = subprocess.run(["docker", "compose", *args], cwd=work, env=env, check=False, text=True, capture_output=True, timeout=timeout)
            if result.returncode:
                raise RuntimeError(result.stderr or result.stdout)
            return result

        try:
            compose("up", "--build", "--detach", "--wait")
            base = f"http://127.0.0.1:{host_ports['MUD_WEB_PORT']}"
            for attempt in range(40):
                try:
                    request(base + "/")
                    break
                except (OSError, urllib.error.URLError):
                    if attempt == 39:
                        raise
                    time.sleep(0.5)

            invitations = {}
            for username in ("alice", "bob"):
                result = subprocess.run(["docker", "compose", "exec", "-T", "mud", "python", "/opt/mudmaker/runtime.py", "invite", username], cwd=work, env=env, check=True, text=True, capture_output=True, timeout=10)
                invitations[username] = result.stdout.strip()
                assert len(invitations[username]) >= 32
            guest_invite = subprocess.run(["docker", "compose", "exec", "-T", "mud", "python", "/opt/mudmaker/runtime.py", "invite", "browserguest"], cwd=work, env=env, check=True, text=True, capture_output=True, timeout=10).stdout.strip()

            bob_cookie, bob = login(base, "bob", invitations["bob"])
            assert bob["state"]["x"] == source["spawn"]["x"] and bob["state"]["y"] == source["spawn"]["y"]
            browser_ui_smoke(base, guest_invite)
            asyncio.run(ssh_chat(host_ports["MUD_SSH_PORT"], ssh_key, base, bob_cookie))
            status, _, state = request(base + "/api/command", "POST", {"command": "inventory"}, bob_cookie)
            assert status == 200 and state["state"]["inventory"] is not None
            print("Exported SSH/browser chat, guided quest/dialogue, installable shell, text-only mode, and offline recovery passed.")
        finally:
            subprocess.run(["docker", "compose", "down", "--volumes", "--remove-orphans"], cwd=work, env=env, check=False, capture_output=True, timeout=60)


if __name__ == "__main__":
    main()
