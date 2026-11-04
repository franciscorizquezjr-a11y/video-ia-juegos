import contextlib
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from scripts import launch_app as launcher


class LauncherTests(unittest.TestCase):
    def test_identity_reuses_only_this_project(self):
        root=Path(tempfile.gettempdir())/"workspace-launch-test"
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.end_headers()
                self.wfile.write(json.dumps({"app":launcher.APP_ID,"workspace_id":launcher.workspace_id(root),"pid":123}).encode())
            def log_message(self,*args):pass
        with ThreadingHTTPServer(("127.0.0.1",0),Handler) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                self.assertEqual(launcher.probe_server(server.server_port,root)["pid"],123)
                self.assertIsNone(launcher.probe_server(server.server_port,root/"other"))
            finally:server.shutdown();thread.join()

    def test_all_ports_busy_raises_instead_of_reusing_busy_port(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0));sock.listen()
            with self.assertRaises(RuntimeError):launcher.find_available_port([sock.getsockname()[1]])

    def test_existing_server_does_not_spawn_another(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing={"url":"http://127.0.0.1:8004/","port":8004,"pid":123}
            with patch.object(launcher,"find_existing",return_value=existing),patch.object(launcher.subprocess,"Popen") as popen:
                self.assertEqual(launcher.start_or_reuse(Path(tmp)),existing)
                popen.assert_not_called()

    def test_start_uses_venv_waits_for_ready_and_saves_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);python=root/".venv/Scripts/python.exe";python.parent.mkdir(parents=True);python.touch()
            ready={"url":"http://127.0.0.1:8007/","port":8007,"pid":321}
            with patch.object(launcher,"find_existing",return_value=None),patch.object(launcher,"find_available_port",return_value=8007),patch.object(launcher,"wait_until_ready",return_value=ready) as wait,patch.object(launcher.subprocess,"Popen") as popen:
                self.assertEqual(launcher.start_or_reuse(root),ready)
                cmd=popen.call_args.args[0]
                self.assertEqual(cmd[0],str(python));self.assertIn("h11",cmd);self.assertNotIn("--reload",cmd)
                self.assertEqual(popen.call_args.kwargs["cwd"],root)
                wait.assert_called_once()
                self.assertEqual(json.loads((root/"logs/launcher_state.json").read_text())["port"],8007)

    def test_readiness_detects_early_server_failure(self):
        process=Mock();process.poll.return_value=1
        with patch.object(launcher,"probe_server",return_value=None),patch.object(launcher,"open_browser") as browser:
            with self.assertRaisesRegex(RuntimeError,"no pudo arrancar"):launcher.wait_until_ready(8004,process)
            browser.assert_not_called()

    def test_chrome_tab_and_fallback(self):
        with patch.object(launcher,"chrome_path",return_value=Path("chrome.exe")),patch.object(launcher.subprocess,"Popen") as popen:
            launcher.open_browser("http://127.0.0.1:8004/")
            self.assertEqual(popen.call_args.args[0][1:],['--new-tab','http://127.0.0.1:8004/'])
        with patch.object(launcher,"chrome_path",return_value=None),patch.object(launcher.webbrowser,"open",return_value=True) as browser:
            launcher.open_browser("http://127.0.0.1:8004/");browser.assert_called_once()

    def test_second_launcher_cannot_take_startup_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"startup.lock"
            with launcher.StartupLock(path) as first:
                self.assertTrue(first.acquired)
                with launcher.StartupLock(path) as second:self.assertFalse(second.acquired)
            with launcher.StartupLock(path) as third:self.assertTrue(third.acquired)

if __name__=="__main__":unittest.main()
