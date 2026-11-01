"""Kommandozeile, z. B. für den ersten Admin:  python -m pinas.cli add-user NAME --admin"""
import argparse
import getpass
import sys

from . import db, users
from .config import load_config, storage
from .helper import SystemHelper


def _ask_password() -> str:
    pw = getpass.getpass("Passwort: ")
    if pw != getpass.getpass("Passwort wiederholen: "):
        sys.exit("Die Passwörter stimmen nicht überein.")
    return pw


def main() -> None:
    p = argparse.ArgumentParser(prog="pinas")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-user", help="Benutzer anlegen")
    a.add_argument("username")
    a.add_argument("--admin", action="store_true")
    s = sub.add_parser("set-password", help="Passwort setzen")
    s.add_argument("username")
    sub.add_parser("list-users", help="Benutzer auflisten")
    args = p.parse_args()

    cfg = load_config()
    db.init_db(cfg["DB_PATH"])
    conn = db.connect(cfg["DB_PATH"])
    helper = SystemHelper(cfg["HELPER"])
    try:
        if args.cmd == "add-user":
            name = users.create_user(conn, helper, storage(cfg)["users"], args.username, _ask_password(), args.admin)
            print(f"Benutzer „{name}“ angelegt{' (Admin)' if args.admin else ''}.")
        elif args.cmd == "set-password":
            users.set_password(conn, helper, args.username, _ask_password())
            print("Passwort geändert.")
        else:
            for row in users.list_users(conn):
                print(f"{row['username']:<32} {'Admin' if row['is_admin'] else ''}")
    except users.UserError as exc:
        sys.exit(f"Fehler: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
