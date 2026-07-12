#!/usr/bin/env python3
"""
ETS Auto — Unified entry point for ETS automation tools.

Usage:
  python run.py gui                 # 启动GUI界面 (default)
  python run.py exam [options]     # 套卷自动答题
  python run.py pk [options]       # 单词PK自动答题
  python run.py --help             # Show help
"""
import argparse
import json
import sys

from ets_common import force_utf8_stdio

force_utf8_stdio()


def _install_log_tee(log_path):
    """Tee stdout/stderr to log_path; return (tee_out, tee_err) or (None, None)."""
    if not log_path:
        return None, None
    from ets_auto import TeeOutput
    tee = TeeOutput(log_path)
    tee_err = TeeOutput(log_path, original_stream=sys.stderr, shared_handle=tee.log)
    sys.stdout = tee
    sys.stderr = tee_err
    return tee, tee_err


def _restore_log_tee(tee, tee_err, log_path):
    if tee_err:
        sys.stderr = tee_err.terminal
    if tee:
        sys.stdout = tee.terminal
        tee.close()
        print("Log saved to: " + log_path)


def main(args_list=None):
    if args_list is None:
        args_list = sys.argv[1:]
    if not args_list or args_list[0] in ('-h', '--help', 'help'):
        print(__doc__)
        print("Commands:")
        print("  gui     启动GUI界面 (launch graphical interface)")
        print("  exam    套卷自动答题 (auto-answer exam questions)")
        print("  pk      单词PK自动答题 (auto-answer word PK)")
        print()
        print("Examples:")
        print("  python run.py")
        print("  python run.py gui")
        print("  python run.py exam --debug")
        print("  python run.py pk --max 50")
        print("  python run.py exam --show-answers")
        sys.exit(0)

    command = args_list[0].lower()
    sub_args = args_list[1:]

    if command == 'gui':
        from ets_gui import ETSApp
        ETSApp().mainloop()

    elif command == 'exam':
        from ets_auto import ETSAutoAnswer

        parser = argparse.ArgumentParser(description="ETS Exam Auto — e听说PC端套卷自动答题")
        parser.add_argument("--max", type=int, default=999, help="Safety limit (default: 999)")
        parser.add_argument("--debug", action="store_true", help="Verbose output for troubleshooting")
        parser.add_argument("--json", action="store_true", help="Output results as JSON")
        parser.add_argument("--show-answers", action="store_true", help="Show all answers without auto-answering")
        parser.add_argument("--log", type=str, default=None, metavar="FILE", help="Save all output to a log file")
        args = parser.parse_args(sub_args)

        tee, tee_err = _install_log_tee(args.log)
        try:
            # stop_event defaulted inside ETSAutoAnswer.ensure_stop_event()
            auto = ETSAutoAnswer(debug_mode=args.debug)
            if args.show_answers:
                auto.connect()
                auto.load_answers()
                auto.show_answers()
                if args.json:
                    print(json.dumps(auto.get_all_answers(), ensure_ascii=False, indent=2))
            else:
                result = auto.run(max_steps=args.max)
                if args.json and result:
                    print(json.dumps(result, ensure_ascii=False))
        finally:
            _restore_log_tee(tee, tee_err, args.log)

    elif command == 'pk':
        from ets_word_pk import ETSWordPK

        parser = argparse.ArgumentParser(description="ETS Word PK Auto v5")
        parser.add_argument("--max", type=int, default=999, help="Max questions")
        parser.add_argument("--debug", action="store_true", help="Show debug info")
        parser.add_argument("--port", type=int, default=10086, help="CDP port")
        parser.add_argument("--log", type=str, default=None, metavar="FILE", help="Save all output to a log file")
        args = parser.parse_args(sub_args)

        tee, tee_err = _install_log_tee(args.log)
        try:
            # stop_event defaulted inside ETSWordPK.ensure_stop_event()
            ETSWordPK(port=args.port, debug_mode=args.debug).run(max_q=args.max)
        finally:
            _restore_log_tee(tee, tee_err, args.log)

    else:
        print("Unknown command: %s" % command)
        print("Use 'python run.py --help' for available commands")
        sys.exit(1)


if __name__ == "__main__":
    main()
