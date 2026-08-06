# Vocabulary block

A fenced code block with info string `vocabulary lang=<lang3> script=<script>`.

Each line is one entry, one of these four shapes:

```
<phrase> = <Polish translation>
<phrase> {<grammar marks>} = <Polish translation>
<phrase> [<transcription>] = <Polish translation>
<phrase> {<grammar marks>} [<transcription>] = <Polish translation>
```

- `<phrase>` is the foreign-language word/phrase in its dictionary
  form (singular nominative for nouns, infinitive for verbs).
- `{<grammar marks>}` is optional. Use only when sure.
- `[<transcription>]` is required for non-Latin scripts (e.g. Arabic,
  Chinese, Hebrew). Skip for Latin/Cyrillic/Greek.
- `= <Polish translation>` is the Polish gloss.

In the lesson JSON that feeds rendering, this first textual slot is stored under
`phrase`.

## Grammar marks

Inside `{...}`, separated by spaces.

| Mark    | Meaning           |
| ------- | ----------------- |
| `N`     | noun              |
| `V`     | verb              |
| `Adj`   | adjective         |
| `Adv`   | adverb            |
| `Prep`  | preposition       |
| `Conj`  | conjunction       |
| `Pron`  | pronoun           |
| `Num`   | numeral           |
| `Part`  | particle          |
| `Interj`| interjection      |
| `sg`    | singular          |
| `pl`    | plural            |
| `m`     | masculine         |
| `f`     | feminine          |
| `n`     | neuter            |
| `nom`   | nominative        |
| `gen`   | genitive          |
| `dat`   | dative            |
| `acc`   | accusative        |
| `inst`  | instrumental      |
| `loc`   | locative          |
| `voc`   | vocative          |

Combine: `{N m sg}`, `{V pf}`, `{Adj f pl}`.

If unsure, omit grammar marks.

## Examples

Latin-script language (German):

````mdx
```vocabulary lang=deu script=latn
das Haus {N n sg} = dom
laufen {V} = biec, biegać
schnell {Adv} = szybko
```
````

Non-Latin script (Arabic) — transcription required:

````mdx
```vocabulary lang=arb script=arab
لماذا {Adv} [limaḏā] = dlaczego
فاض {V} [fāḍa] = wezbrał (o rzece)
نهر الفرات {N m sg} [nahru al-Furāt] = rzeka Eufrat
```
````

## How many entries

15–40, depending on the level — see `phraseforge-core/references/levels.md`. Pick words
central to the text, not high-frequency words the learner already knows.
