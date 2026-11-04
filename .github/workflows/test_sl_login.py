
name: Test Second Life login

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  test-login:
    runs-on: ubuntu-latest
    timeout-minutes: 8

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install Playwright
        run: |
          python -m pip install playwright
          python -m playwright install --with-deps chromium

      - name: Test Second Life login
        env:
          SL_USERNAME: ${{ secrets.SL_USERNAME }}
          SL_PASSWORD: ${{ secrets.SL_PASSWORD }}
          SL_TEST_DATE: '2026-11-20'
        run: python test_sl_login.py

