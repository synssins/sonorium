"""
Sign-in, users, linked devices and keys (docs/AUTH.md).

    config.py       switches from the environment (bypass, ports, config folder)
    passwords.py    Argon2id password hashes
    permissions.py  what each role and key may do, and the permission of every route
    store.py        users, browser sessions, linked devices, keys (auth.json)
    identity.py     server ID and the HTTPS certificate the app pins
    middleware.py   who is calling, and whether they may
    routes.py       sign-in, Users & devices, Link App, pairing
    keys.py         Keys (Settings > Keys), switched on or off in Settings > Advanced
    qr.py           the Link App QR code
    announce.py     mDNS (sonorium.local, _sonorium._tcp) and the LAN addresses for the QR code
"""
