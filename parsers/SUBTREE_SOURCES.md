# Subtree Source Repositories

These vendor directories are now tracked as regular directories (subtree style), not Git submodules.

## Current sources

- `parsers/mineru`
  - URL: `https://github.com/Si-ai-gil/MinerU.git`
  - Branch: `main`
- `parsers/stamp2vec`
  - URL: `git@github.com:Si-ai-gil/stamp2vec.git`
  - Branch: `main`
- `parsers/paddleocr`
  - URL: `git@github.com:ShineiNozen0310/PaddleOCR.git`
  - Branch: `main`

## Suggested update commands

Run from repo root:

```bash
git subtree pull --prefix=parsers/mineru https://github.com/Si-ai-gil/MinerU.git main --squash
git subtree pull --prefix=parsers/stamp2vec git@github.com:Si-ai-gil/stamp2vec.git main --squash
git subtree pull --prefix=parsers/paddleocr git@github.com:ShineiNozen0310/PaddleOCR.git main --squash
```
