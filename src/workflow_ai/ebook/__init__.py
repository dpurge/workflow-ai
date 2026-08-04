"""ebook workflow: schemas, actions, routers, updaters, verifiers.

Importing this package registers everything the `ebook` workflow refers to by
name. Adds or edits a chapter in an ebook project defined by an ebook.yml.
"""

from . import definitions  # noqa: F401 (registers schemas/actions/routers/verifiers)
