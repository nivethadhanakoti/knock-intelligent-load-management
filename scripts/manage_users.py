"""Manage KNOCK console accounts (stored hashed in config/users.json, never in git).

    python scripts/manage_users.py init                       # create demo accounts with random passwords (once)
    python scripts/manage_users.py add NAME ROLE [REGIONS]    # ROLE: viewer | dispatcher | supervisor
                                                              # REGIONS: comma list (la,jhb,adl,chiba,zj) or * for all
    python scripts/manage_users.py list

Passwords are read from the terminal (not echoed) or generated, and printed once. Store them in a password manager.
"""
import getpass, os, secrets, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.security import USERS, ROLES  # noqa: E402

DEMO = [('supervisor', 'supervisor', '*'), ('dispatcher', 'dispatcher', '*'), ('viewer', 'viewer', '*'),
        ('jhb-dispatcher', 'dispatcher', 'jhb')]


def gen() -> str:
    return '-'.join(secrets.token_urlsafe(4) for _ in range(3))


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__); return 0
    cmd = argv[0]
    if cmd == 'init':
        if USERS.count():
            print('users: %d account(s) already exist; nothing created (use add).' % USERS.count()); return 0
        print('Created demo accounts (save these now; they are not shown again):')
        for name, role, regs in DEMO:
            pw = gen()
            USERS.add(name, pw, role, regs.split(','))
            print('  %-15s %-11s regions=%-4s password: %s' % (name, role, regs, pw))
        return 0
    if cmd == 'add' and len(argv) >= 3:
        name, role = argv[1], argv[2]
        if role not in ROLES:
            print('role must be one of', ROLES); return 1
        regions = argv[3].split(',') if len(argv) > 3 else ['*']
        pw = getpass.getpass('Password for %s (blank = generate): ' % name) if sys.stdin.isatty() else ''
        if not pw:
            pw = gen(); print('Generated password:', pw)
        USERS.add(name, pw, role, regions)
        print('Saved %s (%s, regions %s).' % (name, role, ','.join(regions))); return 0
    if cmd == 'list':
        data = USERS._load()['users']
        for n, u in data.items():
            print('%-15s %-11s regions=%s' % (n, u['role'], ','.join(u['regions'])))
        return 0
    print(__doc__); return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
