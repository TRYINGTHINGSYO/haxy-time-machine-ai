"""Read-only Haxy event explorer. Python 3.10+, standard library only."""
import argparse
import collections
import datetime as dt
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap


def clean(value):
    # Public repository text must never execute terminal control sequences.
    return ''.join(c for c in str(value) if c in '\n\t' or (c.isprintable() and c != '\x1b'))


def load_git(repo, ref):
    commit = subprocess.check_output(
        ['git', '-C', repo, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}'],
        text=True).strip()
    raw = subprocess.check_output(['git', '-C', repo, 'log', '--reverse', '--topo-order',
        '--format=%x1e%H%x00%ct%x00%B', commit]).decode('utf-8')
    events = []
    for block in raw.split('\x1e')[1:]:
        oid, timestamp, body = block.split('\x00', 2)
        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            raise ValueError('Non-JSON event commit: ' + oid)
        if not isinstance(event, dict) or not {'id', 'kind', 'data'} <= event.keys():
            raise ValueError('Unsupported event: ' + oid)
        events.append(dict(commit=oid, timestamp=int(timestamp), event=event))
    return events


def replay(events, position):
    records = {}
    for row in events[:position]:
        event = row['event']
        key = (event['kind'], event['id'])
        if event['data'] is None:
            records.pop(key, None)
        else:
            records[key] = dict(id=event['id'], kind=event['kind'],
                data=event['data'], commit=row['commit'], timestamp=row['timestamp'])
    return list(records.values())


def date(timestamp):
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def summary(events, pos):
    records = replay(events, pos)
    issues = [r for r in records if r['kind'] == 'issue']
    return dict(position=pos, total_events=len(events),
        open_issues=sum(r['data'].get('status', 'open') == 'open' for r in issues),
        closed_issues=sum(r['data'].get('status') == 'closed' for r in issues),
        records=records)


def board(events, pos, query=''):
    width = max(44, min(96, shutil.get_terminal_size((80, 24)).columns))
    line = '+' + '-' * (width - 2) + '+'
    def panel(text):
        for part in textwrap.wrap(clean(text), width - 4) or ['']:
            print('| ' + part.ljust(width - 4) + ' |')
    state = summary(events, pos)
    print(line)
    panel('H A X Y   /   T I M E   M A C H I N E       *    .     +')
    panel('A pocket observatory for issues that travel with Git')
    print(line)
    panel(f"REPLAY {pos:03}/{len(events):03}    OPEN {state['open_issues']}    CLOSED {state['closed_issues']}")
    bar_width = width - 20
    filled = int(bar_width * pos / max(1, len(events)))
    panel('[' + '#' * filled + '.' * (bar_width - filled) + ']')
    panel(date(events[pos - 1]['timestamp']) if pos else 'Before the first event')
    print(line)
    for r in state['records']:
        if r['kind'] not in ('issue', 'discuss'):
            continue
        data = r['data']
        if query.casefold() not in json.dumps(data, ensure_ascii=False).casefold():
            continue
        marker = 'x' if data.get('status') == 'closed' else ('?' if r['kind'] == 'discuss' else 'o')
        panel(f"[{marker}] {r['id'][:8]}  {data.get('title', '(untitled)')}")
    print(line)
    print('n/p step | g NUMBER jump | /TEXT search | t timeline | s ID show | q quit')


def timeline(events, pos):
    for i, row in enumerate(events[:pos], 1):
        event = row['event']
        data = event['data']
        label = '(removed)' if data is None else data.get('title', data.get('body', data.get('name', '')))
        label = clean(label).replace('\n', ' ')[:80]
        print(f"{i:3} {row['commit'][:8]} {event['kind']:8} {event['id'][:8]} {label}")


def show(events, pos, prefix):
    records = replay(events, pos)
    matches = [r for r in records if r['id'].startswith(prefix)]
    if len(matches) != 1:
        print('Use an ID prefix matching exactly one active record.')
        return
    record = matches[0]
    print(clean(json.dumps(record, ensure_ascii=False, indent=2)))
    for r in records:
        if r['kind'] == 'comment' and r['data'].get('thread_id') == record['id']:
            print('\nCOMMENT', r['id'][:8], date(r['timestamp']))
            print(clean(r['data'].get('body', '')))


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', help='Read a local Git repository; never fetches or writes')
    p.add_argument('--ref', default='refs/heads/haxy/events')
    p.add_argument('--snapshot', type=Path, default=Path(__file__).with_name('events.json'))
    p.add_argument('--at', type=int, help='Replay only the first N events')
    p.add_argument('--json', action='store_true', help='Print reconstructed state for local tools')
    p.add_argument('--once', action='store_true', help='Print a board and exit')
    p.add_argument('--timeline', action='store_true')
    args = p.parse_args()
    try:
        events = load_git(args.repo, args.ref) if args.repo else json.loads(args.snapshot.read_text(encoding='utf-8'))['events']
        pos = len(events) if args.at is None else args.at
        if not 0 <= pos <= len(events):
            p.error('--at must be between 0 and ' + str(len(events)))
        if args.json:
            print(json.dumps(summary(events, pos), ensure_ascii=False, indent=2))
            return
        if args.timeline:
            timeline(events, pos)
            return
        query = ''
        while True:
            board(events, pos, query)
            if args.once or not sys.stdin.isatty():
                break
            cmd = input('time-machine> ').strip()
            if cmd == 'q':
                break
            elif cmd == 'n': pos = min(len(events), pos + 1)
            elif cmd == 'p': pos = max(0, pos - 1)
            elif re.fullmatch(r'g \d+', cmd): pos = min(len(events), int(cmd[2:]))
            elif cmd.startswith('/'): query = cmd[1:]
            elif cmd == 't': timeline(events, pos)
            elif cmd.startswith('s ') and cmd[2:]: show(events, pos, cmd[2:])
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as e:
        p.exit(1, 'time-machine: ' + clean(e) + '\n')
    except (EOFError, KeyboardInterrupt):
        print('\nObservatory closed. Your Git history is untouched.')


if __name__ == '__main__':
    main()
