#!/usr/bin/env python3
"""Docker entry point; the tools also run as standalone scripts."""
import sys
import convert
import relief
import metadata

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print('Usage: mo-sonar-tools convert {inspect,extract} IMAGE... [options]\n'
              '       mo-sonar-tools relief IMAGE.png --output DIR [options]\n'
              '       mo-sonar-tools metadata IMAGE --output DIR\n'
              'Use convert --help, metadata --help or relief --help for details.')
    elif sys.argv[1] == 'convert':
        sys.exit(convert.main(sys.argv[2:]))
    elif sys.argv[1] == 'metadata':
        sys.exit(metadata.main(sys.argv[2:]))
    elif sys.argv[1] == 'relief':
        sys.exit(relief.main(sys.argv[2:]))
    else:
        print('Unknown command; choose convert, metadata or relief.', file=sys.stderr)
        sys.exit(2)
