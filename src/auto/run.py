#!/usr/bin/env python3
"""
ETS Auto — Unified entry point for ETS automation tools.

Usage:
  python run.py exam [options]     # 套卷自动答题
  python run.py pk [options]       # 单词PK自动答题
  python run.py --help             # Show help
"""
import sys


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help', 'help'):
        print(__doc__)
        print("Commands:")
        print("  exam    套卷自动答题 (auto-answer exam questions)")
        print("  pk      单词PK自动答题 (auto-answer word PK)")
        print()
        print("Examples:")
        print("  python run.py exam --debug")
        print("  python run.py pk --max 50")
        print("  python run.py exam --show-answers")
        sys.exit(0)

    command = sys.argv[1].lower()
    # Remove the command from argv so argparse in sub-modules works correctly
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if command == 'exam':
        from ets_exam import ETSAutoAnswer
        import argparse, json

        parser = argparse.ArgumentParser(description="ETS Exam Auto — e听说PC端套卷自动答题")
        parser.add_argument("--max", type=int, default=999, help="Safety limit (default: 999)")
        parser.add_argument("--debug", action="store_true", help="Verbose output for troubleshooting")
        parser.add_argument("--json", action="store_true", help="Output results as JSON")
        parser.add_argument("--show-answers", action="store_true", help="Show all answers without auto-answering")
        parser.add_argument("--log", type=str, default=None, metavar="FILE", help="Save all output to a log file")
        args = parser.parse_args()

        from ets_exam import TeeOutput
        tee = None
        if args.log:
            tee = TeeOutput(args.log)
            sys.stdout = tee

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
        args = parser.parse_args()

        ETSWordPK(port=args.port, debug_mode=args.debug).run(max_q=args.max)

    else:
        print("Unknown command: %s" % command)
        print("Use 'python run.py --help' for available commands")
        sys.exit(1)


if __name__ == "__main__":
    main()
