"""Settings shared by the Lambdas that aren't per-environment (those come from Terraform env vars)."""

# The chart year when a request doesn't name one. A parameter everywhere else, ready for M4.
TARGET_YEAR = 2026

# The product name, undecided (AOTY-23): a plain description, not a brand. The page's copy is
# scripts/build_shell.py NAME (tests/test_build_shell.py keeps them equal); the chart builder's
# link preview titles use this one (AOTY-85).
NAME = "albums of the year"
