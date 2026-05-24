#!/usr/bin/env python3
"""
ETS Auto — Unified entry point for ETS automation tools.

Usage:
  python run.py exam [options]     # 套卷自动答题
  python run.py pk [options]       # 单词PK自动答题
  python run.py --help             # Show help
"""
import sys
import os


def _force_utf8():
    """Force UTF-8 output on Windows to prevent UnicodeEncodeError with phonetic symbols."""
    if sys.platform == 'win32':
        os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, LookupError):
            pass  # Python <3.7 or unsupported


_force_utf8()


def main(args_list=None):
    if args_list is None:
        args_list = sys.argv[1:]
    if not args_list or args_list[0] in ('-h', '--help', 'help'):
        print(__doc__)
        print("Commands:")
        print("  exam    \u5957\u5377\u81ea\u52a8\u7b54\u9898 (auto-answer exam questions)")
        print("  pk      \u5355\u8bcdPK\u81ea\u52a8\u7b54\u9898 (auto-answer word PK)")
        print()
        print("Examples:")
        print("  python run.py exam --debug")
        print("  python run.py pk --max 50")
        print("  python run.py exam --show-answers")
        sys.exit(0)

    command = args_list[0].lower()
    sub_args = args_list[1:]

    if command == 'exam':
        from ets_auto import ETSAutoAnswer
        import argparse, json

        parser = argparse.ArgumentParser(description="ETS Exam Auto — e听说PC端套卷自动答题")
        parser.add_argument("--max", type=int, default=999, help="Safety limit (default: 999)")
        parser.add_argument("--debug", action="store_true", help="Verbose output for troubleshooting")
        parser.add_argument("--json", action="store_true", help="Output results as JSON")
        parser.add_argument("--show-answers", action="store_true", help="Show all answers without auto-answering")
        parser.add_argument("--log", type=str, default=None, metavar="FILE", help="Save all output to a log file")
        args = parser.parse_args(sub_args)

        from ets_auto import TeeOutput
        tee = None
        tee_err = None
        if args.log:
            tee = TeeOutput(args.log)
            sys.stdout = tee
            tee_err = TeeOutput(args.log, original_stream=sys.stderr, shared_handle=tee.log)
            sys.stderr = tee_err

        try:
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
            if tee_err:
                sys.stderr = tee_err.terminal
            if tee:
                sys.stdout = tee.terminal
                tee.close()
                print("Log saved to: " + args.log)

    elif command == 'pk':
        from ets_word_pk import ETSWordPK
        import argparse

        parser = argparse.ArgumentParser(description="ETS Word PK Auto v5")
        parser.add_argument("--max", type=int, default=999, help="Max questions")
        parser.add_argument("--debug", action="store_true", help="Show debug info")
        parser.add_argument("--port", type=int, default=10086, help="CDP port")
        parser.add_argument("--log", type=str, default=None, metavar="FILE", help="Save all output to a log file")
        args = parser.parse_args(sub_args)

        from ets_auto import TeeOutput
        tee = None
        tee_err = None
        if args.log:
            tee = TeeOutput(args.log)
            sys.stdout = tee
            tee_err = TeeOutput(args.log, original_stream=sys.stderr, shared_handle=tee.log)
            sys.stderr = tee_err

        try:
            ETSWordPK(port=args.port, debug_mode=args.debug).run(max_q=args.max)
        finally:
            if tee_err:
                sys.stderr = tee_err.terminal
            if tee:
                sys.stdout = tee.terminal
                tee.close()
                print("Log saved to: " + args.log)

    else:
        print("Unknown command: %s" % command)
        print("Use 'python run.py --help' for available commands")
        sys.exit(1)


if __name__ == "__main__":
    main()
