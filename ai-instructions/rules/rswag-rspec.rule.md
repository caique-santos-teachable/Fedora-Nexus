---
description: RSpec and Rswag quality guardrails for Fedora — fabricator naming, schema validation, rubocop patterns, and OpenAPI documentation discipline. Use when writing or reviewing rspec request specs, rswag specs, or OpenAPI schema files.
applyTo: "spec/**/*.rb,open_api/**/*.rb,open_api/**/*.yaml"
---

# RSpec & Rswag — Quality Guardrails

## Fabricators

1) Fabricator de attachment — nomes válidos
- **Anti-pattern**: usar `:text_attachment` (não existe). Resulta em `Fabrication::UnknownFabricatorError` e falha imediata de todo o suite.
- **Padrão recomendado**: antes de escrever qualquer `Fabricate(:*_attachment)` em specs rswag, verificar os nomes disponíveis:
  ```bash
  grep -n 'Fabricator(' spec/fabricators/attachment_fabricator.rb
  ```
- **Fabricators válidos** (principais): `:attachment`, `:lecture_attachment`, `:html_attachment`, `:file_attachment`, `:pdf_attachment`, `:image_attachment`, `:wistia_attachment`, `:quiz_attachment`, `:audio_attachment`, `:open_response_attachment`.
- **Para rswag specs de attachments**, o padrão de referência é `:html_attachment` com `position: 1` explícito.

2) Rswag — campos required/non-nullable no schema exigem valor explícito no fabricator
- **Anti-pattern**: fabricar objeto sem setar campo `required: true` e sem `nullable: true` no schema OpenAPI — o valor retornado é `null`, causando falha de validação de schema no `run_test!`.
- **Padrão recomendado**: para cada campo required e non-nullable no schema de resposta, garantir que o fabricator ou o `let!` setam esse campo explicitamente.
- **Checklist de pré-voo para rswag specs**: para cada campo `required: true` e sem `nullable: true` no schema, confirmar que o fabricator o seta por padrão OU que o `let!` passa o valor.
- **Exemplo**:
  ```ruby
  # ❌ position fica null — falha schema validation
  let!(:attachment) { Fabricate(:html_attachment, school: school, attachable: lecture) }

  # ✅ position setado explicitamente
  let!(:attachment) { Fabricate(:html_attachment, school: school, attachable: lecture, position: 1) }
  ```

---

## RSpec Patterns (Rubocop)

3) `rubocop:disable` em spec files — pairing obrigatório e blank line antes do enable
- **Anti-pattern**: `# rubocop:disable CopName` sem `# rubocop:enable CopName` correspondente → gera `Lint/MissingCopEnableDirective`.
- **Anti-pattern paralelo**: `# rubocop:enable` colado à última linha de código sem blank line antes — viola convenção dos arquivos de referência do projeto.
- **Padrão recomendado**: toda diretiva `rubocop:disable` em spec files deve ter `rubocop:enable` como **última linha do arquivo**, precedida por **uma linha em branco**.
- **Verificação rápida antes de concluir**:
  ```bash
  grep -n 'rubocop:disable' <arquivo> && tail -3 <arquivo>
  # Confirmar que a última linha é # rubocop:enable <CopName>
  # e que há uma linha em branco antes dela
  ```
- **Referência de estilo**: `spec/integration/public_api/v2/digital_downloads_spec.rb`
- **Quando aplicar**: sempre que um novo rswag spec file for criado com blocos `describe`/`path` que disparam `RSpec/EmptyExampleGroup`, adicionar disable/enable **no momento da criação do arquivo**, não como fixup posterior.
- **Exemplo**:
  ```ruby
  # ❌ sem enable — gera Lint/MissingCopEnableDirective
  # rubocop:disable RSpec/EmptyExampleGroup
  RSpec.describe "...", ... do
    # ...
  end

  # ✅ enable com blank line antes — padrão correto
  RSpec.describe "...", ... do
    # ...
  end

  # rubocop:enable RSpec/EmptyExampleGroup
  ```

4) RSpec — `ScatteredLet`: `let`/`let!` deve aparecer antes de qualquer `def`
- **Anti-pattern**: declarar `let` ou `let!` após um método `def` dentro do mesmo `describe`/`context` block — dispara `RSpec/ScatteredLet`.
- **Padrão recomendado**: todos os `let`/`let!` devem aparecer **antes** de qualquer `def` helper no mesmo escopo.
- **Verificação**: `rubocop --only RSpec/ScatteredLet <spec_file>`
- **Exemplo**:
  ```ruby
  # ❌ let após def
  def make_request; get path; end
  let(:course) { Fabricate(:course) }

  # ✅ let antes de def
  let(:course) { Fabricate(:course) }
  def make_request; get path; end
  ```

5) Rubocop — `ArgumentAlignment`: argumentos de chamada multi-linha devem ser alinhados
- **Anti-pattern**: argumentos a partir da segunda linha não alinhados com o primeiro argumento após `(` — dispara `Layout/ArgumentAlignment`.
- **Padrão recomendado**: alinhar com o primeiro argumento, ou usar um argumento por linha com indentação consistente de 2 espaços.
- **Verificação**: `rubocop --only Layout/ArgumentAlignment <arquivo>`
- **Exemplo**:
  ```ruby
  # ❌ segundo argumento mal alinhado
  Fabricate(:html_attachment, school: school,
    attachable: lecture, position: 1)

  # ✅ alinhado com o primeiro argumento
  Fabricate(:html_attachment, school: school,
                              attachable: lecture, position: 1)
  # ou um por linha
  Fabricate(
    :html_attachment,
    school: school,
    attachable: lecture,
    position: 1
  )
  ```

6) RSpec — `RSpec/BeEq`: `eq(true)`/`eq(false)` → `be(true)`/`be(false)`
- **Anti-pattern**: usar `expect(x).to eq(false)` ou `expect(x).to eq(true)` — dispara `RSpec/BeEq`.
- **Padrão recomendado**: substituir por `be(false)` / `be(true)` (ou `be_falsey`/`be_truthy` quando apropriado).
- **Exemplo**:
  ```ruby
  # ❌
  expect(result).to eq(false)
  expect(active).to eq(true)

  # ✅
  expect(result).to be(false)
  expect(active).to be(true)
  ```

---

## Rswag / OpenAPI

7) `rake rswag:specs:swaggerize` — sempre restringir com `PATTERN=`
- **Anti-pattern**: rodar `bundle exec rake rswag:specs:swaggerize` sem `PATTERN=` — o rake regenera **todos** os arquivos configurados em `swagger_helper.rb`, sobrescrevendo yamls de outros namespaces com conteúdo apenas das specs executadas.
- **Consequência**: paths de outros namespaces são silenciosamente apagados do yaml correspondente (ex.: 56 paths perdidos do `admin_api/v2/api.yaml` em PR #30994).
- **Padrão recomendado**: sempre usar `PATTERN=` restrito ao namespace trabalhado.
- **Exemplo**:
  ```bash
  # ❌ regenera todos os namespaces — risco de sobrescrever yamls fora do escopo
  bundle exec rake rswag:specs:swaggerize

  # ✅ restrito ao namespace do PR
  bundle exec rake rswag:specs:swaggerize PATTERN='open_api/rswag/end_user_api/**/*_spec.rb'
  ```
- **Verificação pós-geração**: `git diff --stat open_api/` — confirmar que apenas o yaml esperado foi modificado. Se outros yamls aparecerem no diff, restaurar com `git checkout -- <arquivo>`.

8) Rswag — `response '500'` obrigatório em endpoints de mutação (POST/PUT/PATCH/DELETE)
- **Anti-pattern**: rswag spec de endpoint de mutação sem bloco `response '500'` — quando o controller levanta um erro não tratado, o `run_test!` falha com schema validation error.
- **Padrão recomendado**: todo endpoint de mutação deve cobrir pelo menos: código de sucesso (200/201/204), 422/400 (validação) e 500 (erro interno).
- **Verificação**: `grep -n "response '500'" open_api/rswag/<namespace>/<spec>.rb` — todo spec de mutação deve ter hit.
- **Exemplo**:
  ```ruby
  # ❌ sem 500 — falha schema validation quando erro interno ocorre
  response '201', 'transaction created' do
    schema '$ref' => '#/components/schemas/transaction'
    run_test!
  end

  # ✅ com 500 declarado
  response '201', 'transaction created' do
    schema '$ref' => '#/components/schemas/transaction'
    run_test!
  end

  response '500', 'internal server error' do
    schema '$ref' => '#/components/schemas/error'
    run_test!
  end
  ```

9) Rswag — `response '403'` obrigatório em qualquer endpoint com authorization guard
- **Anti-pattern**: rswag spec de endpoint GET/show com authorization check mas sem bloco `response '403'` — schema OpenAPI fica incompleto para consumidores da API.
- **Complemento ao item 8**: o item 8 exige `response '500'` apenas para mutações; este item exige `response '403'` para **qualquer** verb (GET, POST, etc.) que tenha um authorization guard explícito no handler.
- **Checklist**: para cada action, identificar todo `render_unauthorized`/`forbidden_failure` no handler e garantir bloco `response '403'` correspondente no spec.
- **Verificação**:
  ```bash
  grep -n 'render_unauthorized\|403\|not_authorized' app/services/public_api/end_user_api/v2/<handler>.rb
  # Para cada hit, verificar se o rswag spec tem response '403' correspondente
  ```
- **Exemplo**:
  ```ruby
  # ❌ show action com auth guard mas sem response '403'
  get 'Show transaction' do
    response '200', 'transaction found' do
      run_test!
    end
  end

  # ✅ response '403' declarado
  get 'Show transaction' do
    response '200', 'transaction found' do
      run_test!
    end
    response '403', 'forbidden' do
      schema '$ref' => '#/components/schemas/error'
      run_test!
    end
  end
  ```

10) Postman — endpoints DELETE que retornam 204 não devem ter asserções de JSON
- **Anti-pattern**: Postman test de endpoint DELETE asserta `status 200` e valida body JSON (`pm.response.to.be.json`) — a API retorna 204 No Content sem body, causando falha nas asserções.
- **Padrão recomendado**: verificar o status code real da API. Se retorna 204, assertar somente `pm.response.to.have.status(204)` e remover toda asserção de body/JSON.
- **Checklist de pré-voo para Postman DELETE tests**:
  1. Confirmar status code retornado pelo endpoint (204 vs 200).
  2. Se 204: remover `pm.response.to.be.json`, `pm.response.json()` e qualquer `pm.expect` sobre campos do body.
  3. Se 200: manter asserções de body normalmente.
- **Exemplo**:
  ```javascript
  // ❌ 200 + JSON body em endpoint que retorna 204
  pm.test('status code is 200', function () {
    pm.response.to.have.status(200);
  });
  pm.test('response is valid json', function () {
    pm.response.to.be.json;
  });

  // ✅ apenas status 204, sem asserção de body
  pm.test('status code is 204', function () {
    pm.response.to.have.status(204);
  });
  ```
