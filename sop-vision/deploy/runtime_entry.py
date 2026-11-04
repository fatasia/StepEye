"""Frozen web/CLI entry with explicit internal-worker dispatch; never eval argv."""
from __future__ import annotations

import runpy
from pathlib import Path
import sys

WORKERS = frozenset({
    'visual.action_predict', 'visual.label_predict', 'visual.classification_predict',
    'visual.gauge_worker', 'visual.holdout_worker', 'visual.mechanism_valve_worker',
    'visual.mechanism_rul_worker', 'visual.notifications', 'visual.code_reader',
    'visual.training_worker', 'visual.documents_parse', 'visual.mcp_server',
})


def dispatch(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    module, rest = None, []
    if args[:1] == ['-m']:
        if len(args) < 2 or args[1] not in WORKERS:
            raise ValueError('Unsupported internal worker')
        module, rest = args[1], args[2:]
    elif args and Path(args[0]).name == 'documents_parse.py':
        # documents_parse invokes __file__; frozen PYZ does not expose a .py file.
        module, rest = 'visual.documents_parse', args[1:]
    if module is not None:
        old = sys.argv
        try:
            sys.argv = [module, *rest]
            runpy.run_module(module, run_name='__main__', alter_sys=True)
            return 0
        finally:
            sys.argv = old
    from visual.cli import main
    return main(args)


if __name__ == '__main__':
    try:
        raise SystemExit(dispatch())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
