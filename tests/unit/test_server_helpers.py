# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the Pub/Sub webhook server helpers.

These tests cover the pure helper functions in ``expense_agent.server``
without spinning up the FastAPI app or the ADK workflow.
"""

from expense_agent.server import normalize_subscription

# ---------------------------------------------------------------------------
# normalize_subscription
# ---------------------------------------------------------------------------


def test_normalize_subscription_fully_qualified_path():
    """A full Pub/Sub subscription path is reduced to its short name."""
    result = normalize_subscription(
        "projects/my-project/subscriptions/expense-approvals"
    )
    assert result == "expense-approvals"


def test_normalize_subscription_single_segment():
    """A bare subscription name (no slashes) is returned as-is."""
    assert normalize_subscription("expense-approvals") == "expense-approvals"


def test_normalize_subscription_none_returns_default():
    """A None subscription falls back to the default 'pubsub-caller'."""
    assert normalize_subscription(None) == "pubsub-caller"


def test_normalize_subscription_empty_string_returns_default():
    """An empty-string subscription falls back to the default."""
    assert normalize_subscription("") == "pubsub-caller"


def test_normalize_subscription_trailing_slash():
    """A path with a trailing slash yields an empty short name — the last
    non-empty segment is what we want, so this documents the current
    behaviour: rsplit('/', 1)[-1] on 'a/b/' returns ''.
    """
    # This is an edge case — the last segment after rsplit is empty.
    # The function does not special-case trailing slashes; document it.
    result = normalize_subscription("projects/p/subscriptions/")
    assert result == ""


def test_normalize_subscription_long_project_path():
    """A path with a long project id and region is still reduced correctly."""
    result = normalize_subscription(
        "projects/gen-lang-client-0598027295/subscriptions/expense-approvals"
    )
    assert result == "expense-approvals"
    assert "/" not in result
