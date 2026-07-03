"""PhraseForge lesson workflow: schemas, actions, routers, and verifiers.

Importing this package registers everything the `phraseforge` workflow refers to
by name. Mirrors the Pi workflow `phraseforge-mdx.ts` and the phraseforge-web
skill specs.
"""

from . import definitions  # noqa: F401 (registers schemas/actions/routers/verifiers)
