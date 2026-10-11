"""``./run`` waits for its command through Ctrl-C.

Ctrl-C reaches the whole process group. The wrapper used to exit at once and
hand the prompt back while its child was still cleaning up; ``./run ship``
removes its worktrees on the way out, and the prompt must not return before
that is done.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

RUN_SCRIPT = Path(__file__).resolve().parent.parent / "run"


def test_the_wrapper_outlasts_a_child_that_cleans_up_after_ctrl_c(
    tmp_path: Path,
) -> None:
    done = tmp_path / "cleaned-up"
    child = (
        "import signal, sys, time\n"
        "def stop(number, frame):\n"
        "    time.sleep(1)\n"
        f"    open({str(done)!r}, 'w').write('yes')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGINT, stop)\n"
        "print('ready', flush=True)\n"
        "time.sleep(30)\n"
    )
    driver = (
        "import importlib.machinery, importlib.util, sys\n"
        f"loader = importlib.machinery.SourceFileLoader('run_wrapper', {str(RUN_SCRIPT)!r})\n"
        "spec = importlib.util.spec_from_loader('run_wrapper', loader)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "loader.exec_module(module)\n"
        f"sys.exit(module.run_to_the_end([sys.executable, '-c', {child!r}], {{}}))\n"
    )
    wrapper = subprocess.Popen(
        [sys.executable, "-c", driver],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert wrapper.stdout is not None
    assert wrapper.stdout.readline().strip() == "ready"
    time.sleep(0.2)

    # Ctrl-C goes to the whole foreground group. os.killpg rather than
    # `kill -INT -<pid>`, which Linux's kill reads as an option and ignores.
    os.killpg(wrapper.pid, signal.SIGINT)
    returncode = wrapper.wait(timeout=10)

    # The wrapper returned only after the child had finished its cleanup.
    assert done.read_text() == "yes"
    assert returncode == 0
