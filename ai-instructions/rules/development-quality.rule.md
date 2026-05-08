---
description: Global development quality guardrails to reduce syntax, runtime, and performance regressions
applyTo: "**"
---

# Regra Global de Qualidade de Desenvolvimento

Objetivo: reduzir recorrência de erros cross-language em contratos de API, testes e arquivos do sistema de agentes.

## Regra de evolução contínua (obrigatória)

Esta regra deve ser incrementada continuamente.
Sempre que um novo erro de contrato, teste, ou configuração de sistema de agentes for identificado, registrar aqui:
- o anti-pattern observado;
- o padrão recomendado;
- um exemplo curto de correção.

Conteúdo Ruby/Rails → `ruby-rails.instructions.md`
Conteúdo RSpec/Rswag → `rswag-rspec.instructions.md`
Conteúdo Public API V2 → `public-api-v2.instructions.md`

---

1) Novos campos em respostas de tool/endpoint — unit test obrigatório, independente de testes de integração
- **Anti-pattern**: adicionar um campo novo à resposta e cobri-lo apenas por testes de integração com banco de dados marcados como `skip` ou que dependem de infra externa — QA flaga como warning; o campo pode regredir silenciosamente.
- **Padrão recomendado**: todo campo novo em qualquer resposta de tool ou endpoint **deve ter um unit test dedicado** que asserte sua presença e tipo, sem dependência de banco ou serviço externo.
- **Exemplo** (Python MCP):
  ```python
  # ❌ só coberto por DB integration test marcado como skip
  @pytest.mark.skip("requires live DB")
  def test_index_repo_returns_indexed_at(): ...

  # ✅ unit test sem dependência externa
  def test_tool_index_repo_includes_indexed_at(mock_store):
      mock_store.get_indexed_at.return_value = datetime(2026, 5, 4, tzinfo=timezone.utc)
      result = _tool_index_repo("my-repo", store=mock_store)
      assert "indexed_at" in result
      assert result["indexed_at"] == "2026-05-04T00:00:00+00:00"
  ```
- **Regra geral**: testes de integração são complementares, nunca substitutos.
- **Extensão — parâmetros de entrada**: a regra vale também para **novos parâmetros de entrada** em MCP tools (não só campos de resposta). Um novo campo no schema MCP (ex.: `with_symbols`) deve ter um unit test que asserte seu forwarding à camada interna, independente de testes end-to-end.
  ```python
  # ❌ with_symbols adicionado ao schema mas sem unit test de forwarding
  # QA flaga warning — o param pode regredir silenciosamente

  # ✅ unit test verifica forwarding do param
  def test_index_repo_with_symbols_forwarded_to_run_index(mock_run_index):
      call_tool("index_repo", {"repo_id": "my-repo", "with_symbols": True})
      mock_run_index.assert_called_once_with("my-repo", symbol_mode=True)
  ```

2) Skills, agents e rules — escrita obrigatória dentro do repositório em `ai-instructions/`
- **Anti-pattern**: criar arquivos de skill, agent ou rule fora do repositório (ex.: diretamente na pasta global do Copilot, em `.github/`, ou em qualquer path hardcoded de usuário) — esses arquivos ficam fora do controle de versão, não são compartilhados com outros contribuidores e não sobrevivem a reinstalações.
- **Padrão recomendado**: todo arquivo do sistema de agentes **deve ser criado em `ai-instructions/` no repositório**, usando a extensão correta: `*.rule.md`, `*.skill.md`, `*.agent.md`. O `setup.sh` cuida de criar os symlinks para cada ferramenta (Claude, Cursor, Copilot, Windsurf) automaticamente.
- **Regra de ouro**: se o arquivo pertence ao sistema de agentes (não ao código do produto), ele vai para `ai-instructions/` no repo — nunca em path absoluto de usuário.
- **Mapeamento de extensões e destinos** (gerenciado pelo `setup.sh`):
  | Extensão | Destino Claude | Destino Copilot |
  |---|---|---|
  | `*.rule.md` | `~/.claude/rules/<name>.md` | `<prompts>/<name>.instructions.md` |
  | `*.skill.md` | `~/.claude/skills/<name>/SKILL.md` | `<prompts>/<name>.prompt.md` |
  | `*.agent.md` | `~/.claude/agents/<name>.agent.md` | `<prompts>/<name>.agent.md` |
- **Exemplo**:
  ```
  # ❌ path hardcoded de usuário — não versionado, não compartilhável
  ~/Library/Application Support/Code/User/prompts/skills/dead-code/SKILL.md

  # ✅ no repositório — versionado, setup.sh faz o symlink
  ai-instructions/skills/dead-code.skill.md
  ```

3) Parâmetros aceitos mas não implementados — documentar com comentário explícito
- **Anti-pattern**: um método de uma classe abstrata (ex.: `BaseIndexer.index()`) recebe um novo parâmetro opcional (`symbol_mode`) e subclasses que ainda não o implementam o ignoram silenciosamente — sem nenhum comentário. QA flaga inconsistência de comportamento entre subclasses.
- **Padrão recomendado**: toda subclasse que aceita um parâmetro mas não o implementa **deve incluir um comentário inline explícito** explicando a ausência, seguindo o mesmo padrão das subclasses que já o documentam.
- **Exemplo** (Python MCP indexer):
  ```python
  # ❌ TypeScript indexer aceita symbol_mode mas ignora sem comentário
  def index(self, repo_path: str, *, symbol_mode: bool = False) -> Graph:
      ...  # symbol_mode silently unused

  # ✅ comportamento ausente documentado explicitamente
  def index(self, repo_path: str, *, symbol_mode: bool = False) -> Graph:
      # symbol_mode not implemented for TypeScript; file-level only
      ...
  ```
- **Regra**: se Ruby documenta o gap mas TypeScript não → QA vai flagar inconsistência. Padronize o comentário em **todas** as subclasses não-implementadas.

4) Extensão de assinatura em método abstrato com parâmetro keyword-only opcional — preservar backward compat
- **Anti-pattern**: adicionar um novo parâmetro a um método abstrato como argumento posicional — quebra todos os call sites existentes e exige atualização simultânea de todas as subclasses.
- **Padrão recomendado**: novos parâmetros em métodos abstratos devem ser **keyword-only com default** (`*, param=False`). Isso preserva backward compat: subclasses não-atualizadas continuam funcionando; call sites existentes não precisam mudar.
- **Exemplo**:
  ```python
  # ❌ positional — quebra subclasses existentes
  class BaseIndexer:
      def index(self, repo_path: str, symbol_mode: bool) -> Graph: ...

  # ✅ keyword-only com default — zero breaking changes
  class BaseIndexer:
      def index(self, repo_path: str, *, symbol_mode: bool = False) -> Graph: ...
  ```

5) Comentário "not implemented" deixado após a implementação ser feita — remover obrigatoriamente no mesmo ciclo
- **Anti-pattern**: um comentário como `# symbol_mode not implemented for Ruby; file-level only` é mantido no código depois que a feature foi implementada. QA flaga como contradição no ciclo seguinte, forçando um ciclo extra desnecessário.
- **Padrão recomendado**: quando uma feature anteriormente stubada for implementada, a **primeira edição** deve remover ou atualizar o comentário "not implemented". Trate comentários obsoletos desse tipo como bugs, não como cosmética.
- **Checklist**: ao implementar uma feature que substitui um stub → busque (`grep`) a string "not implemented" no arquivo antes de finalizar; remova ou atualize toda ocorrência que se torna falsa.
- **Exemplo** (Python indexer):
  ```python
  # ❌ comentário da versão stub deixado — agora contradiz a implementação
  def index(self, repo_path: str, *, symbol_mode: bool = False) -> Graph:
      # symbol_mode not implemented for Ruby; file-level only  ← stale!
      if symbol_mode:
          return self._index_symbols(...)

  # ✅ comentário removido no mesmo commit da implementação
  def index(self, repo_path: str, *, symbol_mode: bool = False) -> Graph:
      if symbol_mode:
          return self._index_symbols(...)
  ```

6) Listas de hooks/callbacks de framework — incluir TODAS as variantes na passagem inicial
- **Anti-pattern**: ao adicionar reconhecimento de callbacks de um framework (ex.: Rails ActiveRecord), apenas as variantes `before_*` e `after_*` são incluídas; `around_*` são omitidas. Nenhum teste cobre `around_*`, então a lacuna é silenciosa até QA ou um usuário a encontrar.
- **Padrão recomendado**: ao implementar uma lista de hooks nomeados para qualquer framework, enumere **todas** as variantes canônicas da documentação oficial na passagem inicial. Para Rails AR: `before_*`, `after_*` e `around_*` para todos os eventos do ciclo de vida. Adicione ao menos um unit test por família de variante.
- **Regra**: se a lista de hooks for definida como um `set` ou constante, inclua um comentário citando a fonte (ex.: `# https://api.rubyonrails.org/classes/ActiveRecord/Callbacks.html`) para facilitar auditorias futuras.
- **Extensão — hooks específicos de versão**: alguns callbacks não seguem o padrão `before_*/after_*/around_*`. O Rails 7.1 adicionou `before_commit` como variante independente. Ao definir um conjunto de hooks, audite o changelog do framework para identificar adições irregulares e inclua-as explicitamente.
- **Exemplo**:
  ```python
  # ❌ around_* omitido e before_commit (Rails 7.1) ausente — lacunas silenciosas
  _RAILS_HOOK_NAMES = {
      "before_create", "after_create",
      "before_save",   "after_save",
  }

  # ✅ todas as três famílias + hooks específicos de versão presentes
  _RAILS_HOOK_NAMES = {
      "before_create",     "after_create",     "around_create",
      "before_save",       "after_save",       "around_save",
      "before_update",     "after_update",     "around_update",
      "before_destroy",    "after_destroy",    "around_destroy",
      "before_validation", "after_validation", "around_validation",
      "before_commit",     "after_commit",     "around_commit",  # before_commit: Rails 7.1+
  }
  ```

7) Stub substituído por implementação real — testes do novo comportamento obrigatórios no mesmo ciclo
- **Anti-pattern**: um parâmetro como `symbol_mode` existia como stub (no-op, `return []` ou ignorado silenciosamente). O Engineer o implementa de verdade mas não escreve testes, argumentando que "o parâmetro já existia". QA flaga: o comportamento mudou radicalmente — ausência de testes é regressão latente.
- **Padrão recomendado**: sempre que um stub ou no-op for substituído por lógica real, trate como feature nova do ponto de vista de cobertura. Unit tests para cada branch do novo comportamento são obrigatórios **no mesmo ciclo da implementação**, independente de o parâmetro já existir na assinatura.
- **Checklist**: ao implementar um stub → (1) busque `# not implemented`, `# stub`, `return []` no bloco; (2) escreva unit tests para cada branch novo; (3) remova comentários obsoletos (regra 5).
- **Exemplo** (tree-sitter TypeScript `symbol_mode`):
  ```python
  # ❌ symbol_mode era stub (return []), agora implementado mas sem testes — QA flaga
  def _extract_ts_symbols(self, src: str) -> list[str]:
      # era: return []  ← stub anterior removido mas nenhum teste adicionado
      return [n.text for n in root.children if n.type == "function_declaration"]

  # ✅ implementação + testes no mesmo ciclo
  def test_ts_symbol_mode_extracts_function_declarations(mock_ts_repo):
      graph = TreeSitterIndexer().index(mock_ts_repo, symbol_mode=True)
      assert any(n.kind == "function" for n in graph.nodes)

  def test_ts_symbol_mode_false_omits_symbol_nodes(mock_ts_repo):
      graph = TreeSitterIndexer().index(mock_ts_repo, symbol_mode=False)
      assert all(n.kind != "function" for n in graph.nodes)
  ```

9) Error responses with hint/diagnostic content — assert ALL components in the same test
- **Anti-pattern**: an error response contains multiple components (error code + message + hint/actionable suggestion), but the test only asserts the code and core message, leaving the hint unprotected. QA flags the missing hint assertion as an AC coverage gap, forcing an extra cycle.
- **Padrão recomendado**: for every error path that returns a hint, suggestion, or diagnostic prefix alongside the error message, assert **ALL** components in the **same** test: error code, message text, and hint content. Never split these into separate tests.
- **Checklist** when writing a test for an error path:
  1. Assert the error code → required.
  2. Assert the core message text → required.
  3. Does the response include a hint, suggestion, path prefix, or actionable info? → assert it in the same test.
- **Exemplo** (Python MCP `index_repo` path-not-found):
  ```python
  # ❌ hint content (HOST_REPOS_PREFIX) not asserted — AC3 unprotected; regresses silently
  def test_index_repo_returns_not_found_for_missing_path(mock_store):
      result = _tool_index_repo("ghost-repo", store=mock_store)
      assert result["code"] == "NOT_FOUND"
      assert "Directory not found" in result["error"]

  # ✅ ALL error components asserted in the same test
  def test_index_repo_returns_not_found_for_missing_path(mock_store):
      result = _tool_index_repo("ghost-repo", store=mock_store)
      assert result["code"] == "NOT_FOUND"
      assert "Directory not found" in result["error"]
      assert "HOST_REPOS_PREFIX" in result["error"]  # hint present and correct
  ```

8) Error-recovery blocks with retry — test ALL three branches in the same cycle
- **Anti-pattern**: a `try/except` + retry block is only tested for the "recovery" path (error handled → retry succeeds). The "rejection" branch (error outside scope → re-raise immediately) and "escalation" branch (retry also fails → propagate) are left untested. QA flags both as coverage gaps, forcing an extra cycle. Additionally, wrapping the retry in a no-op `try/except RuntimeError: raise` is flagged as dead code.
- **Padrão recomendado**: every error-recovery + retry block has exactly **three** exits that must all be tested in the same cycle as the implementation:
  1. **Rejection** — the error is outside the handled scope and is re-raised immediately (no cleanup, no retry).
  2. **Recovery** — the error is handled, cleanup succeeds, and the retry works.
  3. **Escalation** — the error is handled, cleanup runs, but the retry also fails → exception propagates to caller.
- **Regra**: never wrap a retry call in `try/except ExceptionType: raise` — it adds no behaviour. Let exceptions from retries propagate naturally.
- **Checklist before marking an error-recovery task done**:
  1. Is there a test for the rejection branch (wrong error type → immediate re-raise)? → required.
  2. Is there a test for the recovery branch (error handled + retry OK)? → required.
  3. Is there a test for the escalation branch (error handled + retry also fails)? → required.
  4. Does the retry call have a no-op `try/except` wrapper? → remove it.
- **Exemplo** (Python KuzuDB stale-lock cleanup):
  ```python
  # ❌ Cycle 1 — only recovery tested; rejection and escalation branches missing
  def test_stale_lock_file_removed_on_init(tmp_path, ...):
      # only tests: lock found + removed + retry succeeds
      ...

  # ✅ Cycle 2 — all three branches covered
  def test_non_lock_runtime_error_re_raised(...):
      # rejection: RuntimeError("some other error") propagates immediately, no lock touched
      ...

  def test_stale_lock_file_removed_on_init(...):
      # recovery: lock found + removed + retry succeeds
      ...

  def test_stale_lock_retry_also_fails(...):
      # escalation: lock removed but retry also raises → RuntimeError propagates
      ...
  ```

10) Pytest custom markers — register in `pyproject.toml` before first use
- **Anti-pattern**: a custom marker (e.g. `@pytest.mark.integration`) is added to test files without being declared in `pyproject.toml` — pytest emits `PytestUnknownMarkWarning` on every run; CI configurations that treat warnings as errors will fail; the marker is silently ignored by `-m integration` filter.
- **Padrão recomendado**: every custom marker must be declared under `[tool.pytest.ini_options]` `markers` **in the same cycle** it is first used in a test file. Never use an unregistered marker.
- **Exemplo**:
  ```toml
  # ❌ marker used in tests but not declared — PytestUnknownMarkWarning
  # pyproject.toml has no [tool.pytest.ini_options] markers entry

  # ✅ marker registered in pyproject.toml in the same cycle as first use
  [tool.pytest.ini_options]
  markers = [
      "integration: marks tests that require external services (deselect with '-m not integration')",
  ]
  ```
- **Checklist**: when adding `@pytest.mark.<name>` to any test → immediately check `pyproject.toml` for the marker entry; add it if absent.

11) Dead/legacy code retained after subsystem replacement — mark `# legacy` in the same cycle
- **Anti-pattern**: a custom parser (e.g. Lark Cypher parser) or allowlist constant (`_SUPPORTED_CLAUSES`, `_UNSUPPORTED_CLAUSES`) is replaced by a native engine but the old code is left in place without any marker. Future engineers can't tell if it's still active. QA flags it as dead code in the review cycle, forcing an extra cycle just for a comment.
- **Padrão recomendado**: when a subsystem is replaced and old code is intentionally kept (for rollback documentation or reference), mark **every dead constant, function, and class** with `# legacy — replaced by <new approach>` **in the same cycle as the replacement**. If the code serves no reference value, delete it.
- **Checklist** when replacing a subsystem:
  1. Is the old code referenced by any live callsite? If not → delete in same cycle.
  2. If kept for reference → mark every dead symbol: `# legacy — replaced by native Kuzu Cypher; kept for reference`.
  3. Grep `_SUPPORTED`, `_UNSUPPORTED`, `_ALLOWED`, `_BLOCKED`, `_WHITELIST`, `_BLACKLIST` — common names for dead allowlist constants after parser replacement.
- **Exemplo**:
  ```python
  # ❌ dead constant left without marker — QA flags as active dead code
  _UNSUPPORTED_CLAUSES = {"CREATE", "MERGE", "SET", "DELETE"}

  # ✅ marked in same cycle as replacement
  # legacy — replaced by native Kuzu Cypher; kept for reference
  _UNSUPPORTED_CLAUSES = {"CREATE", "MERGE", "SET", "DELETE"}
  ```

12) State-reset operations — assert ALL cleared caches and counters in the same test
- **Anti-pattern**: a `reset_db()` or similar teardown method clears multiple state containers (DB files, `embedding_cache`, etc.). The test only asserts that the DB was recreated, not that in-memory caches were also cleared. QA flags the missing cache assertions on cycle 2, forcing an avoidable extra cycle.
- **Padrão recomendado**: for every state-reset method, enumerate **all** state it owns (persistent files + in-memory caches + counters) and assert **each one** explicitly in `test_<name>_wipes_and_reinitializes`. Do not rely on a subsequent re-index passing to implicitly verify clearance.
- **Checklist** before marking a reset/teardown done:
  1. Grep `self._` in the store/service class → list every dict, list, set, counter the class owns.
  2. Assert each container is empty/reset in the unit test (`== {}`, `== []`, `== 0`).
  3. Do **not** defer cache-clearance assertions to integration tests — unit-assert empty state explicitly.
- **Exemplo** (Python KuzuStore `reset_db`):
  ```python
  # ❌ only DB existence checked — embedding_cache not asserted → QA flags cycle 2
  def test_reset_db_wipes_and_reinitializes(store, tmp_path):
      store.reset_db()
      assert store.list_repos() == []  # only DB reset checked

  # ✅ ALL state asserted post-reset in the same test
  def test_reset_db_wipes_and_reinitializes(store, tmp_path):
      store._embedding_cache["/repo"] = ("fake", "data")  # pre-populate cache
      store.reset_db()
      assert store.list_repos() == []        # DB recreated
      assert store._embedding_cache == {}    # every in-memory cache cleared
  ```
