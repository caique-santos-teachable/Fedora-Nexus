---
description: "Accumulated guardrails for Public API V2 — anti-patterns and required patterns for handlers, serializers, controllers, rswag specs, and multi-tenancy. Apply whenever implementing or reviewing any Public API V2 endpoint (admin or end-user)."
applyTo: "app/controllers/public_api/**/*.rb,app/services/public_api/**/*.rb,app/serializers/public_api/v2/**/*.rb,spec/requests/public_api/**/*.rb,spec/services/public_api/**/*.rb,open_api/rswag/**/*.rb"
---

# Public API V2 — Accumulated Guardrails

Consult before implementing handlers, serializers, controllers or rswag specs.

## Regra de evolução contínua

Este arquivo deve ser incrementado a cada nova anti-pattern identificada em sessões de desenvolvimento da Public API V2. Seguir o formato numerado: anti-pattern → padrão recomendado → exemplo curto.

---

## 0. Regras gerais para evitar N+1

O N+1 ocorre quando código itera sobre uma coleção e dispara uma query por item. Em handlers V2, os pontos críticos são:

| Situação | Solução |
|---|---|
| Serializer acessa associação em loop | `includes(assoc)` na query do handler |
| Serializer com `case kind` acessa branches polimórficas | `includes` de **todas** as associações referenciadas em **todos** os branches |
| Guard de autorização antes de query principal | `joins` — valida existência em uma query só, não duas |
| Navegação por associações de pai para filho para avô | `joins(parent: :grandparent)` encadeado |
| Filtro por coluna de tabela associada | `joins(:assoc).where(table: { col: val })` |
| Serializer lê dado de associação em todos os itens | `preload` quando `joins` já existe (evita cartesian product) |

**Checklist antes de finalizar um handler:**
1. Listar todas as associações acessadas no serializer correspondente.
2. Garantir que cada uma aparece no `includes`/`preload` da query do handler.
3. Para validações de escopo (school, course, enrollment), usar `joins` + `where`, nunca `find_by` + `exists?` serial.

---

## 1. Serializer com ramificação por `kind` e associações polimórficas — N+1

- **Anti-pattern**: serializer usa `case kind` e acessa `object.attachable` (ou outra associação polimórfica) em um dos branches sem preload — gera N+1 para cada item da coleção que corresponda àquele kind.
- **Padrão recomendado**: toda associação acessada em qualquer branch do `case kind` deve ser listada no `includes` do handler que busca a coleção.
- **Exemplo real** (`lecture_handler.rb`):
  ```ruby
  # ✅ includes de associação acessada no serializer (native_comments_attachment)
  # app/services/public_api/v2/courses/lectures/lecture_handler.rb
  relation = Lecture.where(course_id: course_id).includes(:native_comments_attachment)
  ```
- **Padrão para serializers com `case kind`**:
  ```ruby
  # ❌ N+1 — attachable carregado lazy para cada quiz attachment
  when "quiz"
    lecture = object.attachable  # lazy load para cada item!

  # ✅ handler precarrega todas as associações de todos os branches
  Attachment.where(...).includes(:quiz, :open_response_question, :attachable)
  ```

---

## 1.1 `includes` + `joins` combinados — preload de associação após JOIN de escopo

- **Quando usar**: o `joins` filtra por coluna da associação (escopo/autorização), e o `includes` precarrega os dados dessa mesma ou de outra associação para uso no serializer.
- **Anti-pattern**: usar apenas `joins` e depois acessar a associação no serializer — `joins` não precarrega; gera N+1.
- **Exemplo real** (`pricing_plan_handler.rb`):
  ```ruby
  # ✅ joins filtra por product_id; includes precarrega o produto para o serializer
  # app/services/public_api/v2/pricing_plan_handler.rb
  def relation_for_product(school:, config:, product_id:, apply_default_order: true)
    base_relation(school)
      .includes(config.fetch(:preload_association))   # ex: :course, :digital_product
      .joins(config.fetch(:join_association))          # ex: :course_pricing_option
      .where(config.fetch(:join_table) => { config.fetch(:join_foreign_key) => product_id })
  end
  ```
- **Regra**: se há `joins` numa associação e o serializer também lê dados dela, adicionar `includes` (ou `preload`) da mesma associação. `joins` sem `includes` é JOIN-only — os dados não são carregados em memória.

---

## 1.2 `includes` de múltiplas associações em `base_relation`

- **Quando usar**: o serializer acessa sempre as mesmas associações para todos os itens da coleção.
- **Padrão recomendado**: declarar `includes` direto em `base_relation` para que qualquer chamada (`get_all`, `get_by_id`) já beneficie do eager load.
- **Exemplo real** (`lecture_section_handler.rb`):
  ```ruby
  # ✅ base_relation já precarrega lectures e drip_content
  # app/services/public_api/v2/lecture_section_handler.rb
  def self.base_relation(school, course_id)
    school.lecture_sections
          .where(course_id: course_id)
          .includes(:lectures, :drip_content)
  end
  ```

---

## 1.3 Multi-JOIN encadeado para autorização em queries profundas

- **Quando usar**: a query precisa validar ownership por múltiplos níveis (ex: attachment → lecture → course → school) sem carregar cada nível em memória.
- **Anti-pattern**: `find_by` em cada nível — 3 queries seriais para validar escola + curso + lecture.
- **Padrão recomendado**: `joins` encadeados com `where` na mesma query.
- **Exemplo real** (`lecture_handler.rb` — update/destroy):
  ```ruby
  # ✅ JOIN encadeado: attachment → lecture_section → course; filtra school em uma query
  # app/services/public_api/v2/courses/lectures/lecture_handler.rb
  lecture = Lecture
    .joins(lecture_section: :course)
    .find_by(
      id: id,
      lecture_sections: { course_id: course_id },
      courses: { school_id: school.id }
    )
  ```
- **Sintaxe**: `joins(assoc: :nested_assoc)` + `where(table_name: { col: val })`. O nome da tabela no `where` é o nome plural do model (`lecture_sections`, `courses`).

---

## 1.4 Multi-JOIN com SQL explícito para associações polimórficas ou não-convencionais

- **Quando usar**: o relacionamento não segue convenção Rails (polimórfico, join por tipo, tabela intermediária sem associação declarada).
- **Anti-pattern**: carregar o registro pai em memória e depois filtrar filhos — N+1 ou query dupla.
- **Padrão recomendado**: `joins` com SQL explícito + `preload` para dados necessários no serializer.
- **Exemplo real** (`quiz_response_handler.rb` — multi-join polimórfico):
  ```ruby
  # ✅ 4 JOINs em uma query: quiz_response → custom_form → attachment → lecture → course
  # app/services/public_api/v2/users/quiz_response_handler.rb
  def base_quiz_response_relation(school:, user:)
    base_relation(school)
      .where(user_id: user.id)
      .joins(:custom_form)
      .joins("INNER JOIN attachments ON attachments.id = custom_forms.topic_id AND custom_forms.topic_type = 'Attachment'")
      .joins("INNER JOIN lectures ON lectures.id = attachments.attachable_id AND attachments.attachable_type = 'Lecture'")
      .joins("INNER JOIN courses ON courses.id = lectures.course_id")
      .preload(custom_form: { topic: { attachable: :course } })
      .where(custom_forms: { type: "Quiz" })
  end
  ```
- **Notas**:
  - `joins` com SQL string para associações polimórficas: incluir a condição de `type` no `ON` (`attachable_type = 'Lecture'`).
  - `preload` separado do `joins`: o JOIN filtra, o `preload` carrega os dados em memória para o serializer. Não duplicar — `joins` não precarrega.
  - `preload` aceita hash aninhado: `preload(custom_form: { topic: { attachable: :course } })` = 1 query por nível de associação (4 queries extras, mas N-free).

---

## 1.5 JOIN para validar escopo de attachment sem carregar parent

- **Quando usar**: attachment é polimórfico e precisa ser filtrado por `course_id` sem carregar a lecture.
- **Exemplo real** (`attachment_handler.rb` — `base_relation`):
  ```ruby
  # ✅ JOIN SQL para filtrar attachments pela course_id da lecture associada
  # app/services/public_api/v2/courses/lectures/attachment_handler.rb
  def base_relation(school, course_id, lecture_id)
    Attachment
      .where(school: school, attachable_id: lecture_id, attachable_type: "Lecture")
      .joins("JOIN lectures ON lectures.id = attachments.attachable_id")
      .where(lectures: { course_id: course_id })
      .order(:position)
  end
  ```

---

## 2. Contrato de BaseHandler — `allowed_attributes` obrigatório

- **Anti-pattern**: novo handler herda de `PublicApi::V2::BaseHandler` sem definir `allowed_attributes` — quebra contrato da classe base.
- **Padrão recomendado**: todo handler que herda de `BaseHandler` deve definir `allowed_attributes`, mesmo que retorne `[]`.
  ```ruby
  def allowed_attributes
    []  # ou lista dos atributos permitidos para escrita
  end
  ```

---

## 3. Controller RESTful naming — um recurso por controller

- **Anti-pattern**: adicionar action de sub-recurso em controller pai (ex.: `CoursesController#sections`). Resulta em refactor obrigatório pós-QA e desperdício de ciclo.
- **Padrão recomendado**: criar controller dedicado para cada recurso RESTful. Antes de criar qualquer controller ou handler, buscar o equivalente em `admin_api/v2/` e espelhar a estrutura de diretórios exata.
- **Exemplo**:
  ```ruby
  # ❌ action de sub-recurso no controller pai
  # app/controllers/…/v2/products/courses_controller.rb
  def sections; end

  # ✅ controller dedicado
  # app/controllers/…/v2/products/courses/sections_controller.rb
  def index; end
  ```
- **Verificação**: `grep -r 'def sections\|def lectures\|def attachments' app/controllers/public_api/` — qualquer hit fora de um controller dedicado é um erro estrutural.

---

## 4. End-user API handlers — filtrar por estado de publicação antes de paginar

- **Anti-pattern**: handler end-user retorna `Model.where(...)` sem encadear `.published` — expõe conteúdo rascunho (seções, aulas, attachments) a usuários matriculados.
- **Padrão recomendado**: toda query de coleção em handlers end-user **deve** chamar `.published` antes de paginar.
- **Checklist**: ao escrever ou revisar um handler end-user, verificar que cada `relation =` encadeia `.published` (ou filtro equivalente).
- **Exemplo**:
  ```ruby
  # ❌ expõe rascunhos
  relation = LectureSection.where(course_id: course.id).order(:position)

  # ✅ filtra apenas publicados
  relation = LectureSection.where(course_id: course.id).published.order(:position)
  ```

---

## 5. Specs de endpoint end-user — cobertura de conteúdo não publicado

- **Anti-pattern**: spec cobre apenas happy path com dados publicados; helpers de fabricação não setam `is_published: true` explicitamente, tornando o teste dependente de defaults do banco.
- **Padrão recomendado**: para todo endpoint que filtra por `is_published`, incluir pelo menos um contexto com `is_published: false` e assertar que o ID **não** aparece na resposta.
- **Exemplo**:
  ```ruby
  # ✅ helper explicita published
  def create_section(course: self.course, name: "Section", position: 1)
    Fabricate(:lecture_section, school: school, course: course, name: name, position: position, is_published: true)
  end

  # ✅ teste de exclusão de rascunho
  context "when course has a mix of published and unpublished sections" do
    let!(:draft_section) { Fabricate(:lecture_section, school: school, course: course, is_published: false) }

    it "excludes unpublished sections" do
      make_request
      ids = response.parsed_body["data"].map { |s| s["id"] }
      expect(ids).not_to include(draft_section.id)
    end
  end
  ```

---

## 6. Fabricator com associações implícitas — `school:` obrigatório

- **Anti-pattern**: `Fabricate(:lecture, course: course)` assumindo que `school:` é inferido — o fabricator do Lecture requer `school:` explicitamente, gerando erro de validação na spec.
- **Padrão recomendado**: para qualquer model com `ScopedToSchool`, passar `school:` explicitamente no fabricator mesmo que outras associações o impliquem.
- **Verificação**:
  ```bash
  grep -A 10 'Fabricator(:lecture)' spec/fabricators/lecture_fabricator.rb
  ```
- **Exemplo**:
  ```ruby
  # ❌ school não passado — falha de validação
  Fabricate(:lecture, course: course, lecture_section: section)

  # ✅ school explícito — sempre seguro
  Fabricate(:lecture, school: school, course: course, lecture_section: section)
  ```

---

## 7. Rswag spec — obrigatório para todo novo endpoint de API documentada

- **Anti-pattern**: implementar endpoint em `public_api/` ou `admin_api/` sem criar rswag spec — schema OpenAPI fica desatualizado.
- **Padrão recomendado**: rswag spec deve ser criado no mesmo ciclo, em `open_api/rswag/<api>/v2/<resource>_spec.rb`.
- **Verificação**:
  ```bash
  ls open_api/rswag/end_user_api/v2/products/courses/
  # Deve conter spec para cada controller implementado
  ```

---

## 8. `school_id` obrigatório em guards de autorização (multi-tenant)

- **Anti-pattern**: usar `Enrollment.exists?(user_id: ..., course_id: ...)` sem `school_id:` em handlers end-user — cria falha de isolamento multi-tenant.
- **Padrão recomendado**: toda chamada de `exists?`, `where`, `find_by` usada em guards de autorização em qualquer handler end-user **deve** incluir `school_id:`.
- **Verificação rápida**:
  ```bash
  grep -n 'exists?\|find_by\|\.where(' app/services/public_api/end_user_api/v2/<handler>.rb | grep -v 'school_id'
  # Qualquer hit é candidato a correção
  ```
- **Exemplo**:
  ```ruby
  # ❌ sem school_id — pode vazar dados cross-tenant
  Enrollment.exists?(user_id: current_user.id, course_id: params[:course_id])

  # ✅ school_id presente
  Enrollment.exists?(user_id: current_user.id, course_id: params[:course_id], school_id: school.id)
  ```

---

## 9. Validação de parâmetros retorna 422, não 400

- **Anti-pattern**: spec esperando `have_http_status(:bad_request)` (400) para erros de validação de parâmetro — Rails retorna 422.
- **Padrão recomendado**: usar `have_http_status(:unprocessable_entity)` (422) para validação. Reservar 400 apenas para JSON malformado no body.
- **Exemplo**:
  ```ruby
  # ❌
  expect(response).to have_http_status(:bad_request)

  # ✅
  expect(response).to have_http_status(:unprocessable_entity)
  ```

---

## 10. Schemas duplicados em `public_api_v2.rb`

- **Anti-pattern**: adicionar schema ao hash `schemas:` ou método `*_schema` em `open_api/rswag/swagger_doc_configurations/public_api_v2.rb` sem verificar se já existe — quando duas branches adicionam o mesmo schema, gera `Lint/DuplicateHashKey` + `Lint/DuplicateMethods` após o merge.
- **Verificação obrigatória** antes de criar o PR:
  ```bash
  grep -n 'my_schema_name' open_api/rswag/swagger_doc_configurations/public_api_v2.rb
  # Qualquer hit indica que o schema já existe — reutilizar a definição existente
  ```
- **Checklist de adição de schema**:
  1. `grep -n '<schema_key>:' public_api_v2.rb` — confirmar ausência no hash `schemas:`.
  2. `grep -n 'def <schema_key>_schema' public_api_v2.rb` — confirmar ausência do método helper.
  3. Só após ambos retornarem vazio: adicionar hash key e método.

---

## 11. Read-only handlers — não sobrescrever `model` ou `allowed_attributes`

`def model` e `def allowed_attributes` são **helpers de CRUD do BaseHandler** — existem para acionar `create`, `update` e `destroy`. Um handler que só implementa leitura (`get_all`, `get_by_id`) **não deve** sobrescrevê-los.

- **Anti-pattern**: sobrescrever `model` e `allowed_attributes` em handler que só define `get_all`/`get_by_id`.
- **Padrão recomendado**: incluir apenas os métodos que o handler realmente usa.
- **Checklist**: após escrever um handler, grep por `def model` e `def allowed_attributes` — se não houver `create`, `update` ou `destroy` na mesma classe, remover esses overrides.

```ruby
# ❌ read-only handler com overrides mortos
class LectureHandler < BaseHandler
  def self.model = Lecture
  def self.allowed_attributes = []

  def self.get_all(school:, course_id:, user:)
    # ...
  end
end

# ✅ sem override — read-only handlers não precisam
class LectureHandler < BaseHandler
  def self.get_all(school:, course_id:, user:)
    # ...
  end
end
```

---

## 12. Authorization: merge `find_by` + `exists?` into a single JOIN query

- **Anti-pattern**: autorizar com dois queries seriais — `find_by` no recurso e depois `exists?` na associação do usuário.
- **Padrão recomendado**: colapsar em um único JOIN.
- **Applies to**: todos os handlers EndUserAPI v2 e handlers AdminAPI que guardam recursos filhos atrás de verificação de parent + user.
- **Checklist**: buscar `find_by` seguido de `exists?` em até 3 linhas sobre o mesmo modelo — esse é um candidato a JOIN.

```ruby
# ❌ dois round-trips
course = Course.not_destroyed.find_by(id: course_id, school_id: school.id)
return not_found_failure("Course not found") unless course
unless Enrollment.exists?(course_id: course.id, user_id: user.id, school_id: school.id, is_active: true)
  return not_found_failure("Course not found")
end

# ✅ JOIN único
course = Course.not_destroyed
               .joins(:enrollments)
               .where(id: course_id, school_id: school.id)
               .where(enrollments: { user_id: user.id, school_id: school.id, is_active: true })
               .first
return not_found_failure("Course not found") unless course
```
