# Mini PsyBer-Agent Implementation Task

## 1. Project background

This repository is based on PsyBer-Agent, a psychological behavior-driven user-agent framework for recommendation-system simulation.

The original project contains several advanced components:

* offline prelearning;
* psychological-behavior alignment training;
* OT/UGW alignment;
* fast inference policy models;
* WebSim integration;
* Playwright browser automation;
* API pools;
* large-scale concurrent agents.

For this task, do not attempt to reproduce every advanced component. Implement a minimal, runnable user-agent prototype inside the existing repository.

## 2. Main objective

Build a minimal recommendation-system user Agent that can:

1. load a user profile;
2. observe a set of recommended items;
3. evaluate the items based on user preferences and psychological state;
4. select one item or choose `next_page`;
5. update its memory and psychological state;
6. repeat the interaction for multiple steps;
7. save the complete trajectory to JSON;
8. generate a readable text summary;
9. run locally without requiring WebSim, Playwright, model training, or an external API.

The implementation must preserve the existing project files whenever possible and should be added as an independent minimal prototype.

## 3. Required minimal architecture

Implement these components:

### UserProfile

Store:

* user ID;
* interests;
* disliked categories;
* curiosity;
* initial satisfaction;
* initial boredom.

### RecommendationItem

Each item should contain:

* item ID;
* title;
* categories or genres;
* description.

### UserAgent

The Agent must contain:

* a user profile;
* a dynamic psychological state;
* a memory list;
* an item-scoring method;
* a decision method;
* a psychological-state update method.

The available actions must be:

* `click`;
* `next_page`.

### RecommendationEnvironment

The environment must:

* load the item dataset;
* select a configurable number of candidates per step;
* send candidates to the Agent;
* execute the returned action;
* record each step;
* run for a configurable number of steps;
* save results.

## 4. Decision logic

Start with a deterministic rule-based policy so the project works without an API.

Suggested scoring logic:

* add points when an item category matches a user interest;
* subtract points when an item category matches a disliked category;
* subtract points when the item has already been clicked;
* use curiosity to slightly increase the value of unfamiliar categories;
* use boredom to reduce the value of repeatedly shown categories;
* choose `next_page` when no candidate exceeds a configurable threshold.

Every decision must include a human-readable reason.

Example return value:

```json
{
  "action": "click",
  "item_id": 2,
  "title": "The Matrix",
  "score": 4.2,
  "reason": "The item matches the user's science-fiction and action interests."
}
```

## 5. Dynamic psychological state

Maintain at least:

* `curiosity`;
* `satisfaction`;
* `boredom`.

Example update rules:

* clicking a matching item increases satisfaction;
* seeing repetitive content increases boredom;
* choosing next page can increase boredom and reduce satisfaction;
* curiosity may increase the probability of selecting unfamiliar content.

Keep values within the range `[0, 1]`.

The memory record for every step must contain the psychological state before and after the action.

## 6. Data files

Create small example data files in a clearly named directory.

Suggested structure:

```text
data/mini_agent/user_profile.json
data/mini_agent/items.json
```

Provide at least 10 movie-style recommendation items covering different categories such as:

* science fiction;
* action;
* romance;
* musical;
* technology;
* comedy;
* animation;
* thriller.

## 7. Output files

Every run must create a timestamped output directory, for example:

```text
runs/mini_agent/20260711_150000/
```

It must contain:

```text
memory.json
summary.txt
config.json
```

`memory.json` must include the complete interaction trajectory.

`summary.txt` must include:

* user profile;
* total steps;
* number of clicks;
* number of next-page actions;
* clicked item sequence;
* final psychological state.

## 8. Command-line interface

Create a runnable entry point, preferably:

```text
src/mini_agent.py
```

It must support:

```bash
python src/mini_agent.py --help
python src/mini_agent.py --track 5
python src/mini_agent.py --track 10 --seed 42
```

Recommended arguments:

* `--profile-path`;
* `--items-path`;
* `--track`;
* `--candidate-num`;
* `--seed`;
* `--output-dir`;
* `--decision-threshold`.

## 9. Configuration compatibility

Where practical, allow the prototype to read optional settings from:

```text
settings/mini_agent.yaml
```

Command-line arguments should override YAML values.

Do not break the existing `settings/task.yaml`.

## 10. Optional LLM interface

After the rule-based version works, add an optional decision-provider abstraction:

```python
class DecisionPolicy:
    def decide(...):
        ...
```

Implement:

```text
RuleBasedPolicy
```

Optionally add an `LLMPolicy` placeholder or implementation, but the default program must not require an API key.

The project must continue to run when no API key is configured.

## 11. Code-quality requirements

* Use Python 3.11.
* Add type hints.
* Use clear class and function names.
* Add docstrings where useful.
* Handle missing or malformed input files.
* Use UTF-8 for JSON and text output.
* Avoid hard-coded absolute paths.
* Do not put API keys into the repository.
* Do not modify unrelated modules.
* Prefer Python standard-library dependencies.
* Add dependencies only when genuinely necessary.

## 12. Testing requirements

Add tests covering at least:

1. profile loading;
2. item scoring;
3. repeated-item penalty;
4. next-page behavior;
5. psychological-state bounds;
6. memory output creation;
7. deterministic execution with the same random seed.

Use the repository's existing test framework if available. Otherwise use `pytest`.

Run the tests and report the results.

## 13. Documentation requirements

Add:

```text
docs/mini_agent.md
```

The documentation must explain:

* what the prototype does;
* its architecture;
* how it relates to PsyBer-Agent;
* what was simplified;
* installation;
* execution commands;
* data format;
* output format;
* example run;
* limitations;
* possible future WebSim and LLM integration.

Update the main README with a short section linking to this document, without replacing the original README content.

## 14. Acceptance criteria

The work is complete only when all of the following are true:

* the command runs successfully;
* the Agent completes at least five interaction steps;
* actions include click or next page;
* memory is updated after every step;
* psychological states change over time;
* JSON and text outputs are generated;
* no external API is required for the default run;
* tests pass;
* documentation is present;
* existing project functions are not unnecessarily broken.

## 15. Working method

Before changing code:

1. inspect the repository structure;
2. identify reusable existing components;
3. describe the implementation plan briefly;
4. implement the minimal prototype;
5. run the command;
6. run tests;
7. inspect generated outputs;
8. fix errors;
9. provide a final summary listing changed files, commands used, test results, and remaining limitations.

Do not only provide sample code in chat. Modify the files in the repository and verify the implementation by running it.
