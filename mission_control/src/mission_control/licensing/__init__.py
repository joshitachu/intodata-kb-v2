"""Operator-side licence issuance. Runs on the operator machine only.

The signing key must never be present on a client stack. Instances verify
licences against the public keys pinned in their ``licensing/keys.py``.
"""
