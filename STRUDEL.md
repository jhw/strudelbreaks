# Strudel syntax quirks

Notes on how Strudel's transpiler and runtime differ from plain
JavaScript. Relevant when editing any `*.strudel.js` file in this repo.

## Strings: single vs double quotes

Strudel transpiles user code via `acorn` → AST walk → `escodegen`. During
that walk, **every double-quoted string literal is rewritten into a
`mini("…")` call** (the mini-notation parser from `@strudel/mini`).

So a double-quoted "literal" evaluates to a **`Pattern`** instance (from
`@strudel/core`), not a JS string. Use single quotes when you actually
want a plain JavaScript string:

```js
const tag = 'capture';       // JS string
const beat = "bd sd cp sd";  // Pattern — NOT a string
```

Equivalently, `"bd sd cp sd"` and `mini('bd sd cp sd')` produce the same
Pattern — the transpile just saves you typing.

## `slider(initial, min, max, step)` args must be literal numbers

Strudel scans the source text to render the slider UI *before* evaluating
the code, so the range arguments have to be numeric literals at the call
site:

```js
const rootSlider = slider(0, 0, 15, 1);                // OK
const rootSlider = slider(0, 0, names.length - 1, 1);  // does NOT render correctly
```

If the range needs to match runtime data, the contract has to be enforced
manually — bake the literal in and keep the runtime value consistent with
it (or clamp).

## Lifting a runtime string into a Pattern

Because double-quoted string literals get auto-converted to Patterns at
transpile time, anything built *at runtime* (a pattern string derived
from slider positions, say) is a plain string until we lift it. The idiom
is `.fmap(mini).innerJoin()`:

```js
// breakStr is Pattern<string> — each slider position picks one of our
// pre-formatted mini-notation strings like '{bd sd cp bd}%8'.
const break_ = breakStr
  .fmap(mini)    // Pattern<string> → Pattern<Pattern<event>>
  .innerJoin();  // flatten → Pattern<event>
```

`.fmap(mini)` runs each runtime string through the same parser the
transpiler would have used for a source literal, giving us a
`Pattern<Pattern<event>>`. `.innerJoin()` flattens that back to
`Pattern<event>`, which is what sources and effects expect downstream.

## References

- [Mini Notation — Strudel](https://strudel.cc/learn/mini-notation/)
- [Coding syntax — Strudel](https://strudel.cc/learn/code/)
- [tidalcycles/strudel Technical Manual](https://github.com/tidalcycles/strudel/wiki/Technical-Manual)
- [@strudel/mini — npm](https://www.npmjs.com/package/@strudel/mini)
