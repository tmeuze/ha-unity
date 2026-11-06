"""The example EMQX ACL, evaluated against Unity's topic scheme.

EMQX's file authorizer is first-match-wins. These tests parse docs/emqx-acl-example.conf
and check that each user can do exactly what Unity needs and nothing else: a child
may publish only its own ``<unit>/+/+/set`` (and climate ``.../target/set``) and
``<unit>/status``, read only its own prefix and the hub marker, and never touch
another child, the device bridge or the discovery prefix. Evaluated, not just read,
so a rule-order mistake in the example is caught.
"""
import re
from pathlib import Path

import pytest

from custom_components.unity import topics

ACL = Path(__file__).resolve().parent.parent / "docs" / "emqx-acl-example.conf"

def topic_matches(filt, topic):
    """MQTT filter match: `filt` may contain + and #."""
    fp, tp = filt.split("/"), topic.split("/")
    for i, seg in enumerate(fp):
        if seg == "#":
            return True
        if i >= len(tp):
            return False
        if seg != "+" and seg != tp[i]:
            return False
    return len(fp) == len(tp)


def sub_filter_covered(rule_filt, sub_filt):
    """A subscription filter is covered if every topic it matches is matched
    by the rule filter. Approximation: compare segment by segment."""
    rp, sp = rule_filt.split("/"), sub_filt.split("/")
    for i, rseg in enumerate(rp):
        if rseg == "#":
            return True
        if i >= len(sp):
            return False
        sseg = sp[i]
        if rseg == "+":
            if sseg == "#":
                return False
            continue
        if sseg != rseg:
            return False
    return len(rp) == len(sp)


RULE = re.compile(r"^\{(allow|deny),\s*(.*)\}\.\s*$")


def parse_acl(text):
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("%%"):
            continue
        m = RULE.match(line)
        if not m:
            raise ValueError("unparsed ACL line: " + line)
        action, rest = m.groups()
        who = "all"
        mu = re.search(r'\{username,\s*\{re,\s*"([^"]+)"\}\}', rest)
        if mu:
            who = re.compile(mu.group(1))
            rest = rest.replace(mu.group(0), "", 1)
        else:
            rest = rest.strip()
            rest = re.sub(r"^all\s*,?", "", rest).strip().lstrip(",")
        pubsub = "all"
        for word in ("publish", "subscribe"):
            if re.search(r"\b" + word + r"\b", rest):
                pubsub = word
        topics = []
        mb = re.search(r"\[(.*)\]", rest)
        if mb:
            for t in re.findall(r'(\{eq,\s*)?"([^"]+)"', mb.group(1)):
                topics.append(("eq" if t[0] else "filter", t[1]))
        rules.append((action, who, pubsub, topics))
    return rules


def acl_decision(rules, user, op, topic):
    for action, who, pubsub, topics in rules:
        if who != "all" and not who.search(user):
            continue
        if pubsub != "all" and pubsub != op:
            continue
        if not topics:  # bare {deny, all}
            return action
        for kind, t in topics:
            t = t.replace("${username}", user)
            if kind == "eq":
                hit = (t == topic)
            elif op == "publish":
                hit = topic_matches(t, topic)
            else:
                hit = sub_filter_covered(t, topic) or topic_matches(t, topic)
            if hit:
                return action
    return "deny"


CASES = [
    ("alpha", "subscribe", "alpha/ha/light/+/config", "allow"),
    ("alpha", "subscribe", "alpha/light/x/state", "allow"),
    ("alpha", "subscribe", "ha-unity/hub/status", "allow"),
    ("alpha", "publish", "alpha/light/x/set", "allow"),
    ("alpha", "publish", "alpha/climate/entry/target/set", "allow"),
    ("alpha", "publish", "alpha/status", "allow"),
    ("alpha", "publish", "beta/status", "deny"),
    ("alpha", "publish", "ha-unity/hub/status", "deny"),
    ("beta", "publish", "beta/status", "allow"),
    ("beta", "publish", "alpha/status", "deny"),
    ("alpha", "publish", "alpha/light/x/state", "deny"),
    ("alpha", "publish", "alpha/ha/light/x/config", "deny"),
    ("alpha", "publish", "alpha/climate/x/current", "deny"),
    ("alpha", "subscribe", "beta/#", "deny"),
    ("alpha", "subscribe", "beta/light/x/state", "deny"),
    ("alpha", "publish", "beta/light/x/set", "deny"),
    ("alpha", "subscribe", "zigbee2mqtt/#", "deny"),
    ("alpha", "subscribe", "zigbee2mqtt/alpha/x", "deny"),
    ("alpha", "publish", "zigbee2mqtt/alpha/x/set", "deny"),
    ("alpha", "publish", "zigbee2mqtt/bridge/request/permit_join", "deny"),
    ("alpha", "subscribe", "homeassistant/#", "deny"),
    ("alpha", "publish", "homeassistant/status", "deny"),
    ("alpha", "subscribe", "#", "deny"),
    ("alpha", "subscribe", "$SYS/#", "deny"),
    ("beta", "subscribe", "beta/ha/switch/+/config", "allow"),
    ("beta", "publish", "beta/switch/x/set", "allow"),
    ("beta", "publish", "alpha/light/x/set", "deny"),
    ("beta", "subscribe", "alpha/light/x/state", "deny"),
    ("beta", "subscribe", "zigbee2mqtt/beta/x", "deny"),
    ("hub", "publish", "alpha/ha/light/x/config", "allow"),
    ("hub", "publish", "beta/light/x/state", "allow"),
    ("hub", "subscribe", "alpha/light/x/set", "allow"),
    ("hub", "subscribe", "homeassistant/+/+/config", "allow"),
    ("hub", "publish", "ha-unity/hub/status", "allow"),
    ("hub", "publish", "homeassistant/light/x/config", "deny"),
    ("nobody", "subscribe", "alpha/#", "deny"),
    ("nobody", "publish", "anything/at/all", "deny"),
    # Unity's own climate topics (see climate.py)
    ("alpha", "subscribe", "alpha/climate/kitchen/current", "allow"),
    ("alpha", "subscribe", "alpha/climate/kitchen/target/state", "allow"),
    ("alpha", "publish", "alpha/climate/kitchen/target/set", "allow"),
    ("alpha", "publish", "alpha/climate/kitchen/target/state", "deny"),
    ("alpha", "publish", "alpha/ha/climate/kitchen/config", "deny"),
    ("beta", "publish", "alpha/climate/kitchen/target/set", "deny"),
    ("hub", "publish", "alpha/climate/kitchen/current", "allow"),
    ("hub", "publish", "alpha/ha/climate/kitchen/config", "allow"),
    ("hub", "subscribe", "alpha/climate/kitchen/target/set", "allow"),
]


@pytest.fixture(scope="module")
def rules():
    return parse_acl(ACL.read_text())


@pytest.mark.parametrize("user,op,topic,expected", CASES, ids=[f"{u}-{o}-{t}" for u, o, t, _ in CASES])
def test_acl_decision(rules, user, op, topic, expected):
    assert acl_decision(rules, user, op, topic) == expected


def test_what_unity_subscribes_to_is_allowed_for_the_hub(rules):
    """The hub subscribes to each child's command and climate-setpoint filters."""
    for unit in ("alpha", "beta"):
        assert acl_decision(rules, "hub", "subscribe", topics.command_filter(unit)) == "allow"
        assert acl_decision(rules, "hub", "subscribe", topics.climate_command_filter(unit)) == "allow"


def test_what_a_child_may_publish_is_exactly_what_the_hub_accepts(rules):
    """A child's publish rights line up with the topics the hub will act on."""
    assert acl_decision(rules, "alpha", "publish", "alpha/light/x/set") == "allow"
    assert topics.parse_command_topic("alpha/light/x/set", "alpha") == "light.x"
    assert acl_decision(rules, "alpha", "publish", "alpha/climate/x/target/set") == "allow"
    assert topics.parse_climate_command_topic("alpha/climate/x/target/set", "alpha") == "x"
    # nothing else a child can publish is something the hub would act on
    for t in ("alpha/light/x/state", "alpha/ha/light/x/config", "alpha/climate/x/current"):
        assert acl_decision(rules, "alpha", "publish", t) == "deny"
