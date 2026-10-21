from __future__ import annotations

import json
import random
import threading
from functools import wraps
from pathlib import Path

_POOL_PATH = Path(__file__).resolve().parent / "data" / "intimacy_play_pool.json"
_STATE_PATH = Path(__file__).resolve().parent / "data" / "intimacy_state.json"
_LOCK = threading.RLock()
_ENABLED: set[str] = set()
_SESSIONS: dict[str, dict] = {}
_RECENT_CARDS: dict[str, list[dict]] = {}
_ACTIVE_GENERATIONS: dict[str, int] = {}
_HISTORY_LIMIT = 5
_RNG = random.SystemRandom()
_PREVIEW_FIELDS = (
    "mode",
    "category",
    "subtypes",
    "location",
    "exposure",
    "position",
    "transition_position",
    "restraint",
    "props",
    "clothing_play",
    "psychology",
    "group_dynamic",
    "ejaculation_play",
    "final_finish",
)


def _load_pool() -> dict:
    return json.loads(_POOL_PATH.read_text(encoding="utf-8"))


def _scope(scope_type: str, scope_id: str) -> str:
    return f"{scope_type}:{scope_id}"


def _serialize_session(session: dict) -> dict:
    out = dict(session)
    out["full_seen"] = sorted(str(actor) for actor in (session.get("full_seen") or set()))
    return out


def _persist_state_locked() -> None:
    payload = {
        "version": 1,
        "enabled": sorted(_ENABLED),
        "sessions": {key: _serialize_session(session) for key, session in _SESSIONS.items()},
        "recent_cards": _RECENT_CARDS,
    }
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(_STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STATE_PATH)


def _load_state() -> None:
    if not _STATE_PATH.exists():
        return
    try:
        payload = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    enabled = payload.get("enabled") or []
    sessions = payload.get("sessions") or {}
    recent_cards = payload.get("recent_cards") or {}

    _ENABLED.update(str(key) for key in enabled if str(key).strip())
    for key, session in sessions.items():
        if not isinstance(session, dict):
            continue
        restored = dict(session)
        restored["full_seen"] = {str(actor) for actor in (restored.get("full_seen") or [])}
        _SESSIONS[str(key)] = restored
    for key, cards in recent_cards.items():
        if isinstance(cards, list):
            _RECENT_CARDS[str(key)] = cards[-_HISTORY_LIMIT:]


_load_state()


def _state_payload(key: str) -> dict:
    enabled = key in _ENABLED
    session = _SESSIONS.get(key)
    active = bool(enabled and session and session.get("card"))
    seen_actors = sorted(str(actor) for actor in ((session or {}).get("full_seen") or set()))
    return {
        "enabled": enabled,
        "state": "active" if active else ("armed" if enabled else "off"),
        "has_card": active,
        "seen_actors": seen_actors if active else [],
    }


def set_enabled(scope_type: str, scope_id: str, enabled: bool) -> dict:
    key = _scope(scope_type, scope_id)
    with _LOCK:
        if enabled:
            _ENABLED.add(key)
        else:
            _ENABLED.discard(key)
            _SESSIONS.pop(key, None)
            _RECENT_CARDS.pop(key, None)
            _ACTIVE_GENERATIONS.pop(key, None)
        _persist_state_locked()
        return _state_payload(key)


def get_state(scope_type: str, scope_id: str) -> dict:
    key = _scope(scope_type, scope_id)
    with _LOCK:
        return _state_payload(key)


def get_card_preview(scope_type: str, scope_id: str) -> dict:
    key = _scope(scope_type, scope_id)
    with _LOCK:
        payload = _state_payload(key)
        session = _SESSIONS.get(key)
        card = session.get("card") if payload["has_card"] and session else None
        payload["card"] = (
            {field: card[field] for field in _PREVIEW_FIELDS if field in card}
            if card else None
        )
        payload["reroll_queued"] = bool(session and session.get("pending_card"))
        return payload


def reset_card(scope_type: str, scope_id: str) -> dict:
    key = _scope(scope_type, scope_id)
    with _LOCK:
        _SESSIONS.pop(key, None)
        _persist_state_locked()
        return _state_payload(key)


def _pick_many(values: list, low: int, high: int) -> list:
    if not values:
        return []
    count = min(len(values), _RNG.randint(low, high))
    return _RNG.sample(values, count)


def _disabled(pool: dict, key: str) -> set[str]:
    values = (pool.get("local_disabled") or {}).get(key) or []
    return {str(value).strip() for value in values if str(value).strip()}


def _filter_strings(values: list[str], disabled: set[str]) -> list[str]:
    return [value for value in values if value not in disabled]


def _filter_items(items: list[dict], disabled: set[str]) -> list[dict]:
    return [
        item
        for item in items
        if item.get("label") not in disabled and item.get("id") not in disabled
    ]


def _weighted_choice(items: list, weights: list[float]):
    if not items:
        raise ValueError("cannot choose from an empty sequence")
    total = sum(max(0.0, float(weight)) for weight in weights)
    if total <= 0:
        return _RNG.choice(items)
    needle = _RNG.random() * total
    cursor = 0.0
    for item, weight in zip(items, weights):
        cursor += max(0.0, float(weight))
        if needle <= cursor:
            return item
    return items[-1]


def _semantic_cluster_map(pool: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for cluster, labels in (pool.get("semantic_clusters") or {}).items():
        for label in labels or []:
            out.setdefault(label, cluster)
    return out


def _recent_labels(recent_cards: list[dict]) -> set[str]:
    labels: set[str] = set()
    for card in recent_cards:
        labels.update(card.get("subtypes") or [])
        labels.update(card.get("psychology") or [])
    return labels


def _pick_semantic_many(
    values: list[str],
    low: int,
    high: int,
    *,
    pool: dict,
    recent_cards: list[dict],
) -> list[str]:
    if not values:
        return []
    count = min(len(values), _RNG.randint(low, high))
    cluster_map = _semantic_cluster_map(pool)
    recent = _recent_labels(recent_cards)
    recent_clusters = {cluster_map[label] for label in recent if label in cluster_map}
    chosen: list[str] = []
    chosen_clusters: set[str] = set()

    while len(chosen) < count:
        candidates = [value for value in values if value not in chosen]
        if not candidates:
            break
        weights: list[float] = []
        for value in candidates:
            weight = 1.0
            cluster = cluster_map.get(value)
            if value in recent:
                weight *= 0.18
            elif cluster and cluster in recent_clusters:
                weight *= 0.48
            if cluster and cluster in chosen_clusters:
                weight *= 0.16
            weights.append(weight)
        picked = _weighted_choice(candidates, weights)
        chosen.append(picked)
        cluster = cluster_map.get(picked)
        if cluster:
            chosen_clusters.add(cluster)
    return chosen


def _compatible(items: list[dict], resources: set[str], mode: str | None = None) -> list[dict]:
    out = []
    for item in items:
        if mode and mode not in (item.get("modes") or [mode]):
            continue
        if set(item.get("requires") or []).issubset(resources):
            out.append(item)
    return out


def _props_for_tags(props: list[dict], tags: list[str]) -> list[str]:
    picked: list[str] = []
    for tag in tags:
        candidates = [p for p in props if tag in (p.get("tags") or [])]
        if candidates:
            label = _RNG.choice(candidates)["label"]
            if label not in picked:
                picked.append(label)
    return picked


def _generate_card(mode: str, recent_cards: list[dict] | None = None) -> dict:
    pool = _load_pool()
    recent_cards = list(recent_cards or [])[-_HISTORY_LIMIT:]

    disabled_categories = _disabled(pool, "categories")
    categories = [
        c for c in pool.get("categories", [])
        if mode in (c.get("modes") or [])
        and c.get("id") not in disabled_categories
        and c.get("label") not in disabled_categories
    ]
    if not categories:
        raise ValueError("no enabled intimacy categories")

    recent_category_ids = [card.get("category_id") for card in recent_cards]
    category_weights: list[float] = []
    for candidate in categories:
        cid = candidate.get("id")
        if recent_category_ids and cid == recent_category_ids[-1]:
            weight = 0.16
        elif cid in recent_category_ids[-3:]:
            weight = 0.34
        elif cid in recent_category_ids:
            weight = 0.58
        else:
            weight = 1.0
        category_weights.append(weight)
    category = _weighted_choice(categories, category_weights)
    category_id = category["id"]

    disabled_locations = _disabled(pool, "locations")
    normal_locations = _filter_items(pool.get("locations") or [], disabled_locations)
    if not normal_locations:
        raise ValueError("no enabled intimacy locations")

    exposure = ""
    exposure_active = bool(pool.get("exposure_flavors")) and _RNG.random() < 0.24
    use_exposure_location = False
    exposure_locations = _filter_items(
        pool.get("exposure_locations") or [],
        _disabled(pool, "exposure_locations"),
    )
    if exposure_active and exposure_locations and _RNG.random() < 0.45:
        location = _RNG.choice(exposure_locations)
        use_exposure_location = True
    else:
        location = _RNG.choice(normal_locations)

    resources = set(location.get("resources") or [])
    if exposure_active:
        disabled_exposure = _disabled(pool, "exposure_flavors")
        exposure_candidates = [
            item for item in _filter_items(pool.get("exposure_flavors") or [], disabled_exposure)
            if set(item.get("requires") or []).issubset(resources)
        ]
        if exposure_candidates:
            exposure = _RNG.choice(exposure_candidates)["label"]

    disabled_subtypes = _disabled(pool, "subtypes")
    category_subtypes = _filter_strings(category.get("subtypes") or [], disabled_subtypes)

    regular_positions = _compatible(pool.get("positions") or [], resources, mode)
    group_positions = _compatible(pool.get("group_positions") or [], resources, "group") if mode == "group" else []

    if not regular_positions:
        location = _RNG.choice(normal_locations)
        resources = set(location.get("resources") or [])
        use_exposure_location = False
        regular_positions = _compatible(pool.get("positions") or [], resources, mode)
        group_positions = _compatible(pool.get("group_positions") or [], resources, "group") if mode == "group" else []
        if exposure_active:
            disabled_exposure = _disabled(pool, "exposure_flavors")
            exposure_candidates = [
                item for item in _filter_items(pool.get("exposure_flavors") or [], disabled_exposure)
                if set(item.get("requires") or []).issubset(resources)
            ]
            exposure = _RNG.choice(exposure_candidates)["label"] if exposure_candidates else ""
    if not regular_positions:
        raise ValueError(f"no compatible positions for location {location.get('label')}")

    if mode == "group" and category_id == "group_physical" and group_positions:
        position = _RNG.choice(group_positions)
    elif mode == "group" and group_positions and _RNG.random() < 0.45:
        position = _RNG.choice(group_positions)
    else:
        position = _RNG.choice(regular_positions)

    transition_pool = group_positions if mode == "group" and group_positions else regular_positions
    transition_candidates = [p for p in transition_pool if p.get("label") != position.get("label")]
    transition_position = _RNG.choice(transition_candidates) if transition_candidates else position

    compatible_restraints = _filter_items(
        _compatible(pool.get("restraints") or [], resources),
        _disabled(pool, "restraints"),
    )
    restraint = None
    restraint_chance = 0.62 if category_id == "restraint" else 0.45
    if compatible_restraints and _RNG.random() < restraint_chance:
        restraint = _RNG.choice(compatible_restraints)

    if category_id == "restraint":
        subtypes = _pick_semantic_many(
            category_subtypes, 1, 3, pool=pool, recent_cards=recent_cards
        )
        if restraint and restraint["label"] not in subtypes:
            subtypes.append(restraint["label"])
    elif category_id == "group_physical" and mode == "group":
        generic_group = [
            "双重关注", "同时刺激", "交替进入", "轮换焦点", "双侧围绕",
            "观看与参与切换", "两种不同刺激并行", "一人暂退后再加入", "三人位置自然轮换",
        ]
        generic_group = _filter_strings(generic_group, disabled_subtypes)
        subtypes = [position["label"]]
        if generic_group and _RNG.random() < 0.65:
            extra = _RNG.choice(generic_group)
            if extra not in subtypes:
                subtypes.append(extra)
    else:
        subtypes = _pick_semantic_many(
            category_subtypes, 1, 3, pool=pool, recent_cards=recent_cards
        )

    psych_low, psych_high = (3, 4) if category_id == "psychological" else (2, 3)
    psychology_pool = _filter_strings(
        pool.get("psychology") or [],
        _disabled(pool, "psychology"),
    )
    psychology = _pick_semantic_many(
        psychology_pool, psych_low, psych_high, pool=pool, recent_cards=recent_cards
    )

    props_pool = _filter_items(pool.get("props") or [], _disabled(pool, "props"))
    props: list[str] = []
    if restraint:
        props.extend(_props_for_tags(props_pool, restraint.get("prop_tags") or []))
    if props_pool and _RNG.random() < 0.65:
        extra_prop = _RNG.choice(props_pool)["label"]
        if extra_prop not in props:
            props.append(extra_prop)
    if props_pool and len(props) < 2 and _RNG.random() < 0.25:
        extra_prop = _RNG.choice(props_pool)["label"]
        if extra_prop not in props:
            props.append(extra_prop)

    clothing_subtypes = {
        "制服", "西装/衬衫", "内衣", "丝袜/连裤袜", "高跟鞋",
        "手套", "乳胶", "皮革", "项圈", "面具",
    }
    clothing_props = {
        "领带", "衬衫", "手套", "高跟鞋", "丝袜", "围巾",
        "发带", "长袜", "睡袍腰带",
    }
    clothing_active = bool(
        any(item in clothing_subtypes for item in subtypes)
        or any(item in clothing_props for item in props)
    )
    clothing_play_pool = _filter_strings(
        pool.get("clothing_play") or [],
        _disabled(pool, "clothing_play"),
    )
    clothing_play = _pick_many(clothing_play_pool, 1, 2) if clothing_active and clothing_play_pool else []

    group_pool = _filter_strings(
        pool.get("group_dynamics") or [],
        _disabled(pool, "group_dynamics"),
    )
    group_dynamic = _pick_many(group_pool, 1, 2) if mode == "group" else []

    ejaculation_pool = _filter_strings(
        pool.get("ejaculation_play") or [],
        _disabled(pool, "ejaculation_play"),
    )
    ejaculation_play = ""
    ejaculation_chance = 0.80 if category_id == "messy" else 0.42
    if ejaculation_pool and _RNG.random() < ejaculation_chance:
        ejaculation_play = _RNG.choice(ejaculation_pool)

    final_targets = _filter_strings(
        pool.get("final_finish_targets") or [],
        _disabled(pool, "final_finish_targets"),
    )
    if not final_targets:
        raise ValueError("final_finish_targets is empty")
    if mode == "group":
        relations = pool.get("final_finish_relations_group") or ["先后"]
        final_finish = {
            "a": _RNG.choice(final_targets),
            "b": _RNG.choice(final_targets),
            "relation": _RNG.choice(relations),
        }
    else:
        final_finish = {"target": _RNG.choice(final_targets)}

    return {
        "mode": mode,
        "category_id": category_id,
        "category": category["label"],
        "subtypes": subtypes,
        "location": location["label"],
        "location_id": location["id"],
        "location_resources": sorted(resources),
        "exposure": exposure,
        "exposure_location": bool(use_exposure_location),
        "position": position["label"],
        "position_requires": list(position.get("requires") or []),
        "transition_position": transition_position["label"],
        "transition_position_requires": list(transition_position.get("requires") or []),
        "restraint": restraint["label"] if restraint else "",
        "restraint_requires": list(restraint.get("requires") or []) if restraint else [],
        "props": props,
        "clothing_play": clothing_play,
        "psychology": psychology,
        "group_dynamic": group_dynamic,
        "ejaculation_play": ejaculation_play,
        "final_finish": final_finish,
    }


def _activate_pending_card_locked(key: str) -> None:
    session = _SESSIONS.get(key)
    if not session:
        return
    pending = session.pop("pending_card", None)
    if not pending:
        return

    current = session.get("card")
    recent = _RECENT_CARDS.setdefault(key, [])
    if current:
        recent.append(current)
        if len(recent) > _HISTORY_LIMIT:
            del recent[:-_HISTORY_LIMIT]

    session["card"] = pending
    session["full_seen"] = set()
    _persist_state_locked()


def _begin_generation(scope_type: str, scope_id: str, mode: str) -> bool:
    key = _scope(scope_type, scope_id)
    with _LOCK:
        session = _SESSIONS.get(key)
        if (
            key not in _ENABLED
            or not session
            or not session.get("card")
            or session.get("mode") != mode
        ):
            return False
        _ACTIVE_GENERATIONS[key] = _ACTIVE_GENERATIONS.get(key, 0) + 1
        return True


def _end_generation(scope_type: str, scope_id: str) -> None:
    key = _scope(scope_type, scope_id)
    with _LOCK:
        count = _ACTIVE_GENERATIONS.get(key, 0)
        if count <= 1:
            _ACTIVE_GENERATIONS.pop(key, None)
            _activate_pending_card_locked(key)
        else:
            _ACTIVE_GENERATIONS[key] = count - 1


def generation_guard(scope_type: str, mode: str):
    """Freeze the active intimacy card for one generation task.

    If the user requests a reroll while the task is still running, the newest
    card is queued and becomes active only after the task finishes. This keeps
    both sequential group responders on the same card.
    """
    def decorator(func):
        @wraps(func)
        async def wrapped(scope_id, *args, **kwargs):
            tracked = _begin_generation(scope_type, str(scope_id), mode)
            try:
                return await func(scope_id, *args, **kwargs)
            finally:
                if tracked:
                    _end_generation(scope_type, str(scope_id))
        return wrapped
    return decorator


def start_session(scope_type: str, scope_id: str, mode: str) -> dict:
    key = _scope(scope_type, scope_id)
    with _LOCK:
        if key not in _ENABLED:
            raise ValueError("intimacy mode is not enabled")

        previous = _SESSIONS.get(key)
        recent = _RECENT_CARDS.setdefault(key, [])

        # A group reply generates two AI responses sequentially. If the user
        # rerolls while that generation task is running, do not replace the
        # live card underneath the second responder. Queue the newest reroll
        # and activate it after the whole generation task exits.
        if (
            mode == "group"
            and _ACTIVE_GENERATIONS.get(key, 0) > 0
            and previous
            and previous.get("card")
            and previous.get("mode") == mode
        ):
            preview_recent = list(recent)
            preview_recent.append(previous["card"])
            previous["pending_card"] = _generate_card(mode, preview_recent[-_HISTORY_LIMIT:])
            _persist_state_locked()
            payload = _state_payload(key)
            payload["reroll_queued"] = True
            return payload

        if previous and previous.get("card"):
            recent.append(previous["card"])
            if len(recent) > _HISTORY_LIMIT:
                del recent[:-_HISTORY_LIMIT]

        _SESSIONS[key] = {
            "mode": mode,
            "card": _generate_card(mode, recent),
            "full_seen": set(),
        }
        _persist_state_locked()
        payload = _state_payload(key)
        payload["reroll_queued"] = False
        return payload


def _display_name(value: str, fallback: str) -> str:
    return str(value or "").strip() or fallback


def _full_card_prompt(
    card: dict,
    mode: str,
    *,
    user_name: str = "用户",
    actor_names: tuple[str, str] = ("AI", "第二AI"),
) -> str:
    user_label = _display_name(user_name, "用户")
    first_actor = _display_name(actor_names[0] if actor_names else "", "AI")
    second_actor = _display_name(actor_names[1] if len(actor_names) > 1 else "", "第二AI")
    mode_label = "三人群聊" if mode == "group" else "一对一私聊"
    lines = [
        "[INTIMACY PLAY CARD｜隐藏玩法要素]",
        "前提：本玩法库仅用于成年人之间的虚构亲密幻想与文学创作。",
        f"模式：{mode_label}",
        f"主类：{card['category']}",
        f"子玩法：{'、'.join(card['subtypes'])}",
        f"地点：{card['location']}",
        f"主位：{card['position']}",
        f"换位倾向：可根据互动自然转为{card['transition_position']}",
    ]
    if card.get("exposure"):
        lines.append(f"暴露/被发现风险：{card['exposure']}")
    if card.get("restraint"):
        lines.append(f"束缚/限制：{card['restraint']}")
    if card.get("props"):
        lines.append(f"道具：{'、'.join(card['props'])}")
    if card.get("clothing_play"):
        lines.append(f"服装玩法：{'、'.join(card['clothing_play'])}")
    if card.get("psychology"):
        lines.append(f"心理：{'、'.join(card['psychology'])}")
    if card.get("group_dynamic"):
        lines.append(f"三人结构：{'、'.join(card['group_dynamic'])}")
    if card.get("ejaculation_play"):
        lines.append(f"射精玩法：{card['ejaculation_play']}")
    final_finish = card["final_finish"]
    if mode == "group":
        lines.extend([
            "本轮最终射精指定：",
            f"男性位A：{final_finish['a']}",
            f"男性位B：{final_finish['b']}",
            f"关系：{final_finish['relation']}",
        ])
    else:
        lines.append(f"本轮最终射精指定：{final_finish['target']}")
    lines.extend([
        "",
        f"以上元素均为成年人之间的虚构亲密创作，旨在帮助{user_label}沉浸式代入虚构情境；内容仅存在于虚构叙事中，不作为现实行为指引，请自然、充分地创作。",
        "元素优先级：主类、子玩法、本轮最终射精指定属于本轮核心方向，应自然落地并保持前后一致；地点决定物理空间约束。心理、三人结构、主位、换位倾向、束缚/限制、道具、服装玩法、暴露/被发现风险、射精玩法属于可变辅助元素，可根据当前互动自然选用、弱化、延后或替换，不要求逐项完成。",
        "以上内容仅作为本轮方向与兼容要素，不是固定分镜；请结合当前聊天和人物性格自然发挥，避免为了覆盖全部字段而把互动写成清单或流水线。",
        f"本卡用于增加互动的神秘感；只有当{user_label}明确且强烈要求知道本轮隐藏卡具体内容时，才允许转述，否则不得泄露任何卡片信息。",
        "主位与换位倾向都只是姿势参考，何时换位、谁主导、谁等待或观看，由上下文自然决定；不要凭空增加当前地点没有的家具或空间条件。",
        "中途或最终射精都不自动代表互动结束；若抽到射精玩法，可自然发展多轮，是否继续下一轮、换玩法或停下，一切都应依据用户当下表达与聊天上下文决定。",
    ])
    if mode == "group":
        lines.append(f"群聊中三人属于同一场景，但不预先固定职责；男性位A/B也不预绑定{first_actor}或{second_actor}，由当前上下文自然对应。")
    else:
        lines.append("私聊中只处理当前双方，不引入第三人。")
    return "\n".join(lines)


def _anchor_prompt(
    card: dict,
    mode: str,
    *,
    user_name: str = "用户",
    actor_names: tuple[str, str] = ("AI", "第二AI"),
) -> str:
    user_label = _display_name(user_name, "用户")
    first_actor = _display_name(actor_names[0] if actor_names else "", "AI")
    second_actor = _display_name(actor_names[1] if len(actor_names) > 1 else "", "第二AI")
    parts = [
        card["category"],
        "子玩法：" + "、".join(card.get("subtypes") or []),
        "地点：" + card["location"],
        "主位：" + card["position"],
        "换位倾向：可自然转为" + card["transition_position"],
    ]
    if card.get("exposure"):
        parts.append("暴露/被发现风险：" + card["exposure"])
    if card.get("restraint"):
        parts.append("限制：" + card["restraint"])
    if card.get("clothing_play"):
        parts.append("服装玩法：" + "、".join(card["clothing_play"]))
    if card.get("psychology"):
        parts.append("心理：" + "、".join(card["psychology"]))
    if card.get("group_dynamic"):
        parts.append("三人结构：" + "、".join(card["group_dynamic"]))
    if card.get("ejaculation_play"):
        parts.append("射精玩法：" + card["ejaculation_play"])
    final_finish = card["final_finish"]
    if mode == "group":
        parts.append(
            "本轮最终射精：A=" + final_finish["a"]
            + " / B=" + final_finish["b"]
            + " / " + final_finish["relation"]
        )
    else:
        parts.append("本轮最终射精：" + final_finish["target"])
    extra = f"群聊三人同场；A/B不预绑定{first_actor}或{second_actor}，角色分工继续由上下文自然决定。" if mode == "group" else "只处理当前双方。"
    return (
        "[当前亲密玩法仍有效]\n"
        + "｜".join(parts)
        + f"\n优先级保持不变：主类、子玩法、本轮最终射精指定是核心；地点只约束物理空间；其余心理、三人结构、姿势、束缚、道具、服装玩法、暴露和射精玩法都是可变辅助，不必逐项兑现。继续顺着当前聊天自然发展，不机械执行清单，不引入与地点冲突的家具。主位与换位倾向都只是姿势参考，何时换位由上下文自然决定。中途射精不自动结束；本轮最终射精指定只规定这一轮的最终去向/关系。完成后也不要自动结束，是否继续下一轮或自然停下完全依据用户当下表达与聊天上下文决定。本卡用于增加互动的神秘感；只有当{user_label}明确且强烈要求知道本轮隐藏卡具体内容时，才允许转述，否则不得泄露任何卡片信息。"
        + extra
    )


def build_play_prompt(
    scope_type: str,
    scope_id: str,
    mode: str,
    *,
    actor: str = "",
    user_name: str = "用户",
    actor_names: tuple[str, str] = ("AI", "第二AI"),
) -> str:
    key = _scope(scope_type, scope_id)
    actor_key = (actor or "default").strip().lower()
    with _LOCK:
        if key not in _ENABLED:
            return ""
        session = _SESSIONS.get(key)
        if not session or not session.get("card") or session.get("mode") != mode:
            return ""
        card = session["card"]
        full_seen = session.setdefault("full_seen", set())
        if actor_key not in full_seen:
            full_seen.add(actor_key)
            _persist_state_locked()
            return _full_card_prompt(
                card,
                mode,
                user_name=user_name,
                actor_names=actor_names,
            )
        return _anchor_prompt(
            card,
            mode,
            user_name=user_name,
            actor_names=actor_names,
        )
