#!/usr/bin/env python3
"""Verify local development through a fresh, private MCP stdio instance.

Run on a Mac with the release's installed MCP Python:
    python -B tests/local_development_acceptance.py /absolute/report/directory

When WAITING_FOR_CHROME_INTEGRATION is printed, open connection.json's URL in
the independently connected ChatGPT Chrome integration on this Mac, replace
the form text with its expected_text, check the test-data checkbox, and submit.
The script deliberately has no browser automation dependency or browser tool.
It uses temporary frontend/backend projects and synthetic Office documents;
it never edits the user's project, running tunnel, keys or OS permissions.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import platform
import secrets
import socket
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

RESOURCES = Path(__file__).resolve().parents[1]
SERVER_SOURCE = RESOURCES / "server" / "src"
sys.path.insert(0, str(SERVER_SOURCE))
from sugar_local_mcp import __version__ as VERSION
from sugar_local_mcp.diagnostics import request_local

TOOLS = {"workspace_list", "list_directory", "read_file", "search_text",
         "write_file", "edit_file", "run_command", "process_list", "read_process",
         "send_process_input", "stop_process", "macos_permissions_status",
         "macos_permissions_request", "office_status", "office_verify"}

SERVICE = r'''from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json, os, sys
from pathlib import Path
role, nonce = sys.argv[1:3]
class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, code, value):
        data = json.dumps(value,ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def do_OPTIONS(self): self.reply(200,{'ok':True})
    def do_GET(self):
        if self.path == '/api/health':
            self.reply(200,{'ok':True,'role':role,'nonce':nonce})
        else: super().do_GET()
    def do_POST(self):
        if role != 'backend' or self.path != '/api/verify':
            return self.reply(404,{'ok':False})
        size = int(self.headers.get('Content-Length','0'))
        if size < 0 or size > 16384: return self.reply(413,{'ok':False})
        body = json.loads(self.rfile.read(size).decode())
        if body.get('nonce') != nonce: return self.reply(400,{'ok':False})
        receipt = {'ok':True,'role':role,'received':body}
        Path('receipt.json.tmp').write_text(json.dumps(receipt,ensure_ascii=False))
        os.replace('receipt.json.tmp','receipt.json')
        self.reply(200,receipt)
server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
print(json.dumps({'port':server.server_address[1],'role':role,'nonce':nonce}),flush=True)
server.serve_forever()
'''

HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>Sugar 0.5.0 · 本机开发验收</title>
<style>*{box-sizing:border-box}body{margin:0;background:#edf5f1;color:#193d32;font:17px/1.65 system-ui}
main{max-width:800px;margin:42px auto;padding:34px;background:white;border:1px solid #c6ddcf;border-radius:20px}
small{color:#476d60}h1{font-size:29px;margin:8px 0 20px}label{display:block;margin:14px 0}
input[type=text]{display:block;font:inherit;padding:11px;width:100%;border:1px solid #94b5a4;border-radius:8px}
button{font:inherit;background:#21664d;color:white;border:0;border-radius:8px;padding:11px 24px;cursor:pointer}
output{display:block;margin-top:24px;padding:20px;background:#edf5f1;border-radius:10px;white-space:pre-wrap}
code{font-size:13px}</style><main><small>SUGAR LOCAL MCP 0.5.0 + CHATGPT CHROME INTEGRATION</small>
<h1>本机终端与本机 Chrome 开发联调</h1>
<p>前端、后端由 Sugar 的两个项目目录启动；网页操作由 ChatGPT 的 Chrome 集成完成。</p>
<p>本次唯一标记：<code id="nonce">NONCE_VALUE</code></p>
<form id="form"><label>中文填写与替换<input id="name" type="text" value="需要替换的旧内容" autocomplete="off"></label>
<label><input id="agree" type="checkbox"> 使用虚构测试数据</label>
<button type="submit">提交到本机后端</button></form>
<output id="result" aria-live="polite">等待 Chrome 集成操作</output>
<small>此页面和所有数据仅用于一次性验收。</small></main>
<script>const nonce='NONCE_VALUE';
document.querySelector('#form').addEventListener('submit',async e=>{
e.preventDefault();const output=document.querySelector('#result');
try{const body={nonce,name:document.querySelector('#name').value,agreed:document.querySelector('#agree').checked};
const response=await fetch('http://127.0.0.1:BACKEND_PORT/api/verify',{
method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const result=await response.json(); if(!response.ok || !result.ok)throw new Error('Backend rejected submission');
output.textContent='本机后端已收到：'+result.received.name+'\n已确认虚构数据：'+result.received.agreed+'\n唯一标记：'+result.received.nonce;
output.dataset.ready='true'; console.log('sugar050-local-backend-verified',result.received.nonce);
}catch(error){output.textContent=String(error);console.error(error);}});
console.log('sugar050-ready',location.origin,nonce);</script></html>'''


def payload(result):
    if result.is_error:
        raise AssertionError("\n".join(getattr(x, "text", "") for x in result.content)[:1600])
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(next(x.text for x in result.content if x.type == "text"))


class Acceptance:
    def __init__(self, output):
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.report = {"version": VERSION, "macos": platform.mac_ver()[0],
            "architecture": platform.machine(), "stages": {}, "failures": [],
            "browser_control": "external ChatGPT Chrome integration",
            "user_projects_changed": False, "permissions_requested": False,
            "production_tunnel_restarted": False}
        self.started = time.monotonic()
        self.jobs = []
        self.ports = []
        self.stage_name = "setup"

    def save(self):
        self.report["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
        self.report["passed"] = not self.report["failures"]
        temp = self.output / "report.json.tmp"
        temp.write_text(json.dumps(self.report, ensure_ascii=False, indent=2))
        os.replace(temp, self.output / "report.json")

    @asynccontextmanager
    async def stage(self, name):
        self.stage_name = name
        detail = self.report["stages"].setdefault(name, {})
        print(f"STAGE {name}", flush=True)
        try:
            yield detail
        except Exception as error:
            detail.update(state="failed", error=f"{type(error).__name__}: {error}")
            self.report["failures"].append({"stage": name, "error": detail["error"]})
            raise
        else:
            detail.setdefault("state", "passed")
        finally:
            self.save()

    async def call(self, name, args=None, timeout=30):
        return payload(await self.session.call_tool(name, args or {}, read_timeout_seconds=timeout))

    async def start(self, workspace, argv, timeout=300):
        job = await self.call("run_command", {"workspace": workspace, "argv": argv,
                              "timeout_seconds": timeout})
        self.jobs.append(job["job_id"])
        return job["job_id"]

    async def drain(self, job, timeout=12):
        deadline, cursor, stdout = time.monotonic() + timeout, 0, ""
        while time.monotonic() < deadline:
            result = await self.call("read_process", {"job_id": job, "cursor": cursor, "max_bytes": 65536})
            cursor = result["next_cursor"]
            stdout += "".join(x["text"] for x in result["events"] if x["stream"] == "stdout")
            if result["output_complete"] and not result["has_more"]:
                assert not result["process_running"] and result["cleanup_error"] is None, result
                return result, stdout
            await asyncio.sleep(0.05)
        raise AssertionError(f"Process output/cleanup did not finish: {job}")

    async def service(self, role, nonce):
        job = await self.start(role, [sys.executable, "-B", "-u", "serve.py", role, nonce], 360)
        deadline, cursor, stdout = time.monotonic() + 10, 0, ""
        while time.monotonic() < deadline:
            result = await self.call("read_process", {"job_id": job, "cursor": cursor})
            cursor = result["next_cursor"]
            stdout += "".join(x["text"] for x in result["events"] if x["stream"] == "stdout")
            if "\n" in stdout:
                item = json.loads(stdout.splitlines()[0])
                assert item["role"] == role and item["nonce"] == nonce
                self.ports.append(item["port"])
                return item["port"]
            assert result["process_running"], result
            await asyncio.sleep(0.05)
        raise AssertionError(f"{role} did not announce a listening port")

    async def checks(self, state):
        async with self.stage("version_tools_and_optional_permissions") as info:
            init = await self.session.initialize()
            assert init.server_info.version == VERSION == "0.5.0"
            names = sorted(x.name for x in (await self.session.list_tools()).tools)
            assert set(names) == TOOLS, names
            workspaces = await self.call("workspace_list")
            assert {x["id"] for x in workspaces["workspaces"]} == {"frontend", "backend"}
            permissions = await self.call("macos_permissions_status")
            ipc = await asyncio.to_thread(request_local, state, "permissions_status")
            assert ipc.get("ok") is True and ipc["pid"] == permissions["pid"] != os.getpid()
            info.update(server_version=init.server_info.version, tool_count=len(names), tools=names,
                        permissions_ipc_same_process=True, socket_mode=oct((state/"desktop-control.sock").stat().st_mode & 0o777))

        async with self.stage("files_stdin_and_timeout_cleanup") as info:
            written = await self.call("write_file", {"workspace":"frontend", "path":"probe.txt",
                "content":"STATUS_PENDING\n中文文件编辑验证\n", "expected_sha256":"missing"})
            await self.call("edit_file", {"workspace":"frontend", "path":"probe.txt",
                "old_text":"STATUS_PENDING", "new_text":"STATUS_READY", "expected_sha256":written["sha256"]})
            read = await self.call("read_file", {"workspace":"frontend", "path":"probe.txt"})
            assert read["content"] == "STATUS_READY\n中文文件编辑验证\n"
            stale = await self.session.call_tool("write_file", {"workspace":"frontend", "path":"probe.txt",
                "content":"must not replace", "expected_sha256":written["sha256"]})
            assert stale.is_error
            job = await self.start("backend", [sys.executable,"-B","-c","import sys; print(sys.stdin.read(), end='')"], 20)
            await self.call("send_process_input", {"job_id":job, "text":"标准输入中文测试\n", "close":True})
            result, stdout = await self.drain(job)
            assert result["exit_code"] == 0 and stdout == "标准输入中文测试\n"
            timed = await self.start("backend", [sys.executable,"-B","-c","import time; time.sleep(60)"], 1)
            result, _ = await self.drain(timed)
            assert result["state"] == "timed_out" and result["exit_code"] is not None
            info.update(chinese_read_edit=True, stale_hash_rejected=True, stdin_and_eof=True,
                        timeout_exit_code=result["exit_code"], timeout_cleanup_error=result["cleanup_error"])

        async with self.stage("office_synthetic_roundtrips") as info:
            status = await self.call("office_status", timeout=40)
            verified = await self.call("office_verify", {"include_libreoffice":False}, timeout=60)
            info.update(detection=status, verification=verified)
            # Missing optional libraries are a reported capability, not an MCP failure.
            formats = verified["verification"]["formats"]
            assert set(formats) == {"xlsx", "docx", "pptx"}, formats
            assert all(x.get("state") in {"verified", "missing"} for x in formats.values()), formats
            info["verified_formats"] = [name for name, value in formats.items() if value["state"] == "verified"]
            info["unavailable_formats"] = [name for name, value in formats.items() if value["state"] == "missing"]

        async with self.stage("two_projects_and_external_chrome") as info:
            nonce = secrets.token_hex(12)
            for role in ("frontend", "backend"):
                await self.call("write_file", {"workspace":role, "path":"serve.py", "content":SERVICE,
                                               "expected_sha256":"missing"})
            backend = await self.service("backend", nonce)
            await self.call("write_file", {"workspace":"frontend", "path":"index.html",
                "content":HTML.replace("NONCE_VALUE",nonce).replace("BACKEND_PORT",str(backend)),
                "expected_sha256":"missing"})
            frontend = await self.service("frontend", nonce)
            connection = {"url":f"http://127.0.0.1:{frontend}/index.html", "nonce":nonce,
                          "expected_text":"Sugar 0.5.0：Chrome 集成与本机前后端联调通过"}
            (self.output/"connection.json").write_text(json.dumps(connection,ensure_ascii=False,indent=2))
            info.update(connection)
            self.save()
            print("WAITING_FOR_CHROME_INTEGRATION " + json.dumps(connection,ensure_ascii=False),flush=True)
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                receipt = await self.session.call_tool("read_file", {"workspace":"backend", "path":"receipt.json"})
                if not receipt.is_error:
                    data = json.loads(payload(receipt)["content"])
                    assert data["ok"] and data["role"] == "backend"
                    assert data["received"] == {"nonce":nonce, "name":connection["expected_text"], "agreed":True}, data
                    info.update(backend_receipt=data, matched_same_machine_nonce=True)
                    print("BROWSER_BACKEND_VERIFIED",flush=True)
                    # Keep the page alive briefly for the independent browser's screenshot.
                    await asyncio.sleep(15)
                    break
                await asyncio.sleep(1)
            else:
                raise AssertionError("No valid submission from the external Chrome integration within 240 seconds")

    async def cleanup(self):
        details = self.report.setdefault("cleanup", {})
        for job in reversed(self.jobs):
            try:
                result = await self.call("read_process", {"job_id":job})
                if result["process_running"]:
                    await self.call("stop_process", {"job_id":job})
                result, _ = await self.drain(job)
                details[job] = {"stopped":True,"exit_code":result["exit_code"],"cleanup_error":result["cleanup_error"]}
            except Exception as error:
                details[job] = {"stopped":False,"error":str(error)[:800]}
                self.report["failures"].append({"stage":"cleanup","error":str(error)[:800]})
        for port in self.ports:
            with socket.socket() as client:
                client.settimeout(1)
                assert client.connect_ex(("127.0.0.1",port)) != 0, f"Owned port {port} is still listening"
        details["owned_ports_closed"] = True
        self.save()

    async def run(self):
        if sys.platform != "darwin": raise RuntimeError("This acceptance run requires macOS")
        with tempfile.TemporaryDirectory(prefix="sugar050-",dir="/tmp") as temporary:
            root = Path(temporary).resolve()
            state = root / "state"
            projects = {name:root/name for name in ("frontend","backend")}
            for path in projects.values(): path.mkdir()
            config = root / "workspaces.json"
            config.write_text(json.dumps({"state_dir":str(state),"workspaces":[
                {"id":name,"root":str(path),"allow_write":True,"allow_exec":True} for name,path in projects.items()]}))
            config.chmod(0o600)
            env = {k:v for k,v in os.environ.items() if k in {"HOME","USER","LOGNAME","PATH","TMPDIR","LANG","LC_ALL","LC_CTYPE","TZ","SHELL"}}
            env.update(PYTHONDONTWRITEBYTECODE="1",PYTHONPATH=str(SERVER_SOURCE))
            params = StdioServerParameters(command=sys.executable,args=["-B","-m","sugar_local_mcp","serve","--config",str(config)],env=env)
            with (self.output/"server-stderr.log").open("w") as errlog:
                async with asyncio.timeout(420):
                    async with stdio_client(params,errlog=errlog) as (reader,writer):
                        async with ClientSession(reader,writer) as session:
                            self.session=session
                            try:
                                await self.checks(state)
                            finally:
                                await self.cleanup()
            self.report["cleanup"]["ipc_socket_removed"] = not (state/"desktop-control.sock").exists()
            assert self.report["cleanup"]["ipc_socket_removed"]
        self.report["cleanup"]["temporary_projects_removed"] = not root.exists()
        self.save()
        return 0 if self.report["passed"] else 1


def main():
    if len(sys.argv)!=2 or not Path(sys.argv[1]).is_absolute():
        print("Usage: local_development_acceptance.py /absolute/report/directory",file=sys.stderr)
        return 2
    suite=Acceptance(Path(sys.argv[1]))
    try: result=asyncio.run(suite.run())
    except BaseException as error:
        suite.report["failures"].append({"stage":suite.stage_name,"error":f"{type(error).__name__}: {error}"})
        suite.save(); result=1
    print(json.dumps(suite.report,ensure_ascii=False,indent=2),flush=True)
    return result


if __name__=="__main__": raise SystemExit(main())
