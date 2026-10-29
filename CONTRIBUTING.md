# Contributing to FirstTake

Thank you for wanting to make it better. FirstTake was built on real shoots, and the most
valuable contributions come from real shoots too.

## The most useful things you can send

- **A quiet failure you hit.** Something that reported success and wasn't, with the numbers.
  [`docs/tutorials/07-quiet-failures.md`](docs/tutorials/07-quiet-failures.md) is the model.
- **A camera, recorder or workflow it doesn't handle yet** — open an issue with what you shot on,
  what you expected, and what happened.
- **Fixes and new tools** as pull requests.

## Pull requests

1. Keep a change to one idea, and say in the description what you measured to know it works.
2. Run the self-tests for anything you touched (`python3 scripts/<tool>.py --selftest`; the full
   list is in [`scripts/README.md`](scripts/README.md)) and say which ones you ran.
3. Run `python3 scripts/scrub_check.py` before you push. Never commit client names, footage,
   credentials or anything that identifies a real shoot.
4. Sign off your commits (`git commit -s`), certifying the
   [Developer Certificate of Origin](https://developercertificate.org/): that you wrote the change
   or otherwise have the right to submit it under this project's license.

By contributing, you agree that your contribution is licensed under the Apache License 2.0, as
the project is (section 5 of [LICENSE](LICENSE)). Sklarstad LLC remains the copyright holder of
the project as a whole, and contributors keep the copyright on their own contributions.

## Releasing (maintainers)

🔴 **Bump `version` in both `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`
(keep them equal) in any PR that should reach installed copies.** `claude plugin update` compares that field, not the commits: 1.0.0 stayed 1.0.0 through
two merged PRs, and every installed copy reported "already at the latest version" while missing
both. Patch for fixes and docs, minor for a new tool or backend.

## Questions

Open a GitHub issue or discussion, or email [support@sklarstad.com](mailto:support@sklarstad.com)
