---
title: "Models miss the middle of a long prompt."
date: 2026-10-20
level: Foundations
slug: lost-in-the-middle
summary: "A detail buried in the center of a long prompt is easier to skip than one at either end. Putting the important fact first or last keeps it in play."
---

# Models miss the middle of a long prompt.

You paste a long note into a chatbot and it answers from the start and the end. The sentence you cared about was in the middle, and it never comes back.

## How it works

A model reads the whole prompt at once, then weighs some tokens more than others. The start and the end get more of that weight. The middle gets less, which is why a buried fact disappears.

## The numbers

One preprint found that accuracy fell when the answer sat in the middle of the context and rose again when the same fact moved to the end. Treat that figure as author-reported.

## An analogy: a crowded notebook

The first and last lines of a notebook page are the ones you see when you flip through. The middle line needs a finger on it. The analogy breaks when the page is short, because then every line is in view.

## Try this

Paste a ten-sentence note into a chatbot and hide one concrete fact in sentence five. Ask for that fact. Then move it to sentence one and ask again.

## Use it at dinner

"If you bury the point in the middle of a long prompt, the model is the reader who skips that line."

## Sources

- [Lost in the Middle (arXiv)](https://arxiv.org/abs/2307.03172)
- [The transformer paper (arXiv)](https://arxiv.org/abs/1706.03762)
