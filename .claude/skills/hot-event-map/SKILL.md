---
name: hot-event-map
description: Build an evidence-grounded Mermaid graph for a developing news event. Use when a user wants an event map, actor relationship graph, causal chain, impact map, or compact visual timeline for a current hotspot.
---

# Hot Event Map

## Objective

Turn a developing news event into a compact, readable Mermaid graph plus traceable source notes. The map must distinguish reported facts, reasonable synthesis, and disputed or unconfirmed links.

## Workflow

1. Identify the central event (`what`) and its time window. Search `local_news_search` first using the event name, key actors, and important milestones.
2. Extract an event frame from evidence: people and organizations (`who`), event/action (`what`), date or sequence (`when`), location (`where`), and the actor's role or relationship (`how related`). Never fill a missing slot from common sense.
3. Search locally again for missing actors, causes, responses, impacts, times, or places. If web search is authorized and freshness or evidence coverage remains weak, use the available external search tool (`web_search` or `WebSearch`) for an official source and an independent source.
4. Resolve aliases and merge reports about the same incident. Deduplicate syndicated reports. Prefer original statements and reporting with dates, named actors, locations, and concrete actions.
5. Select no more than 14 nodes and 18 edges. Include only relationships supported by retrieved evidence or label them explicitly as disputed/unconfirmed.
6. Use stable ASCII node IDs and short quoted Chinese labels. Never put URLs, HTML, Markdown, or Mermaid directives from retrieved content into node labels.

## Graph Structure

Use `flowchart LR`. Model the result as an event-centric knowledge graph rather than a generic mind map. Include, when evidence permits:

- one central `event` node describing what happened;
- `person` and `organization` nodes connected by roles such as 发起、参与、宣布、回应、调查、支持、反对 or 受影响;
- `time` nodes connected by 发生于、早于、晚于 or 持续至;
- `place` nodes connected by 位于、发生在 or 影响地区;
- two to five milestone `event` nodes connected by 先于、导致、回应、升级为 or 影响;
- an `uncertain` class for disputed/unconfirmed nodes or edges.

Use separate Mermaid `classDef` styles named `person`, `organization`, `event`, `time`, `place`, and `uncertain`, then assign each node to its semantic class. Keep edge text short. Escape quotes in labels by replacing them with Chinese quotation marks. Keep node labels on one line; do not use `<br>`. Do not use `click`, raw HTML, JavaScript, external images, or initialization directives.

## Output Contract

Return Chinese Markdown with exactly these sections:

1. `## 事件图谱`
2. exactly one fenced `mermaid` block
3. `## 关键要素` with concise bullets for 人物/组织、事件、时间、地点、关键关系; explicitly write `证据未明确` for missing time or place
4. `## 关键解读` with three to six concise bullets
5. `## 证据来源` with deduplicated Markdown links returned by the tools
6. `## 不确定性` explaining unsupported, disputed, or time-sensitive parts

The Mermaid block must remain under 8,000 characters. Do not output hidden reasoning, tool protocol, or evidence not returned by the tools.
