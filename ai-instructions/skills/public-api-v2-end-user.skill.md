---
name: public-api-v2-end-user-routes
description: Use this skill when implementing or reviewing Public API V2 End User endpoints. Covers routes, namespaces, controllers, handlers, query filters, serializers, response helpers, OpenAPI and rswag patterns.
---

# Public API V2 End User Routes Skill

Use this skill to implement new End User API V2 endpoints with the same conventions used by `courses`, `digital_downloads`, `attachments`, `transactions`, and `purchases`.

## 1) Scope and canonical references

Primary references in this repository:

- `app/controllers/public_api/end_user_api/v2/application_controller.rb`
- `app/controllers/public_api/end_user_api/v2/products/courses_controller.rb`
- `app/controllers/public_api/end_user_api/v2/products/digital_downloads_controller.rb`
- `app/controllers/public_api/end_user_api/v2/products/digital_downloads/attachments_controller.rb`
- `app/controllers/public_api/end_user_api/v2/transactions_controller.rb`
- `app/controllers/public_api/end_user_api/v2/purchases_controller.rb`
- `app/controllers/concerns/public_api/v2/shared_response_helpers.rb`
- `app/services/public_api/end_user_api/v2/course_handler.rb`
- `app/services/public_api/end_user_api/v2/digital_download_handler.rb`
- `app/services/public_api/end_user_api/v2/digital_downloads/attachment_handler.rb`
- `app/services/public_api/end_user_api/v2/transaction_handler.rb`
- `app/services/public_api/v2/base_handler.rb`
- `app/services/public_api/v2/queries/query_processor.rb`
- `open_api/public_api/end_user_api/v2/api.yaml`
- `open_api/rswag/end_user_api/v2/*.rb`

## 2) Route design rules (mandatory)

Define End User V2 endpoints under:

- `config/routes.rb` → `namespace :public_api` → `namespace :end_user_api` → `namespace :v2` → `scope path: :current_user`

Pattern:

- Keep URL contract under `/v2/current_user/...` (docs/mock flow) and map to End User V2 controllers.
- Use `namespace :products` for product resources.
- Use nested `scope module: :<resource>` when child resources live in subfolders.

Example shape:

```ruby
namespace :v2 do
  scope path: :current_user do
    get "me", to: "users#me"

    namespace :products do
      resources :courses, only: [:show]
      resources :digital_downloads, only: [:index, :show] do
        scope module: :digital_downloads do
          resources :attachments, only: [:index]
        end
      end
    end

    resources :transactions, only: [:index, :show]
    resources :purchases, only: [:show]
  end
end
```

## 3) Namespace and folder parity

URL, file path and Ruby namespace must match.

- `.../products/digital_downloads/{id}/attachments`
- `app/controllers/public_api/end_user_api/v2/products/digital_downloads/attachments_controller.rb`
- `module PublicApi::EndUserApi::V2::Products::DigitalDownloads`

Apply the same parity for handlers, query filters/sorting, and serializers.

## 4) Controller rules (thin controller, hard requirement)

Controller responsibilities:

1. Scope enforcement (`require_authorized_scopes`)
2. Parameter extraction/permit
3. Handler call
4. Uniform render path (`render_data`, `render_paginated_data`, `render_from_error`)

Do not put business logic in controllers.

Conventions to follow:

- Inherit from `PublicApi::EndUserApi::V2::ApplicationController`.
- Declare handler and serializer constants at class top.
- Implement `resource_name` when using `render_from_error`.
- For list endpoints:
  - call handler with `page: params[:page]`, `limit: params[:per_page]`
  - success -> `render_paginated_data(data, serializer: XSerializer)`
- For show endpoints:
  - success -> `render_data(XSerializer.new(result.value))` or `.as_json` when needed
- Failure always via `render_from_error(result.value)` (or local alias variable).

## 5) Scope/auth rules

Every endpoint must define least-privilege scopes:

- `courses:read`
- `digital_downloads:read`
- `transactions:read`
- `purchases:read`
- profile: `email:read` and `name:read`

Use:

```ruby
require_authorized_scopes %w[scope:read]
```

or `only:` when action-specific.

## 6) Handler rules (business and data access)

Handlers should:

- Inherit from `PublicApi::V2::BaseHandler`.
- Return monadic `Success` / `Failure` payloads.
- Scope all reads by `school` and (for end-user data) by `user`.
- Validate ownership/access with `exists?` checks before loading nested collections.

Expected failure contract for `render_from_error` compatibility:

- `reason:` one of `:not_found`, `:invalid_request`, `:validation_failed`, `:validation_error`, `:forbidden`, `:conflict`
- optional `message:`, `errors:`, `error_code:`

For index/list with filtering/sorting/pagination, prefer:

- `PublicApi::V2::Queries::QueryProcessor.apply(...)`
- pass `filter_class`, optional `sorting_class`, `page`, `limit`.

## 7) Query filters/sorting rules

For non-trivial list endpoints, define dedicated query classes:

- `.../queries/current_user/<resource>/filters.rb`
- `.../queries/current_user/<resource>/sorting.rb`

Conventions:

- Filters inherit `PublicApi::V2::Queries::BaseFilters`.
- Sorting inherits `PublicApi::V2::Queries::Sorting`.
- Keep `ALLOWED_FIELDS` explicit.
- Keep filters deterministic and side-effect free.
- Avoid N+1 by preloading in handler relation when serializer accesses associations.

## 8) Serializer rules

Serializer contract must mirror OpenAPI schema exactly.

Conventions:

- Use `ActiveModel::Serializer`.
- Declare fields with `attribute`.
- Compute nested resource refs (`href`, related ids/types) in serializer methods.
- Explicitly normalize booleans where needed (`!!value`).
- Keep URL generation consistent with current contract (`/api/v2/current_user/...` or `/v2/current_user/...` according to existing endpoint family).

If serializer is product-like, evaluate inheriting from `PublicApi::V2::BaseProductSerializer`.

## 9) Response contract rules

Use `PublicApi::V2::SharedResponseHelpers` conventions:

- success list:
  - `render_paginated_data(...)` -> `{ data: [...], meta: {...} }`
- success item:
  - `render_data(...)` -> `{ data: {...} }`
- errors:
  - `render_from_error(...)` -> `{ error: { code, message, details } }`

Do not manually build error JSON in controllers unless there is a very specific exception.

## 10) Parameter handling rules

In controller:

- Extract only accepted filter params via `params.permit(...).to_h.symbolize_keys`.
- Keep path params explicit in handler calls (`id`, `digital_download_id`, etc.).
- Keep pagination input mapping consistent:
  - request: `page`, `per_page`
  - handler/query_processor arg: `limit: params[:per_page]`

## 11) OpenAPI and rswag rules

For each new endpoint:

1. Update `open_api/public_api/end_user_api/v2/api.yaml`
2. Add/update rswag spec in `open_api/rswag/end_user_api/v2/<resource>_spec.rb`
3. Keep schema and runtime output aligned:
   - envelope (`data`, `meta`, `error`)
   - required fields
   - nullable fields
   - status codes

Minimum rswag scenarios:

- success
- forbidden (missing scope) when applicable
- not found for member endpoints
- validation/invalid request when endpoint has date/filter/pagination validation

## 12) Testing rules (request/service/serializer)

When adding/changing endpoint behavior:

- request specs under `spec/requests/public_api/end_user_api/v2/...`
- service specs for new handler/query logic
- serializer specs for non-trivial transformations

For request-level contracts, ensure host and oauth/kong stubbing pattern matches current v2 tests.

## 13) fedora-nexus CLI protocol (for safe changes)

Before changing files, run:

1. `fedora-nexus blast-radius $REPO_PATH <changed_files...> --json`
2. `fedora-nexus deps $REPO_PATH <file> --depth 2 --json`
3. `fedora-nexus dependents $REPO_PATH <file> --json`

Use this to:

- detect collateral impact,
- verify shared helpers (`shared_response_helpers`, `base_handler`, `query_processor`) risk,
- decide required test breadth.

## 14) Implementation template (copy and adapt)

Controller skeleton:

Reference file: `app/controllers/public_api/end_user_api/v2/products/digital_downloads_controller.rb`

```ruby
module PublicApi::EndUserApi::V2::<Namespace>
  class <ResourcesController> < PublicApi::EndUserApi::V2::ApplicationController
    require_authorized_scopes %w[<resource:read>]

    Handler = PublicApi::EndUserApi::V2::<Handler>
    Serializer = PublicApi::<...>::<Serializer>

    def index
      result = Handler.get_all(
        school: current_school,
        user: current_user,
        filters: filter_params,
        page: params[:page],
        limit: params[:per_page]
      )

      data = result.value
      if result.success?
        render_paginated_data(data, serializer: Serializer)
      else
        render_from_error(data)
      end
    end

    private

    def resource_name
      "<Resource>"
    end

    def filter_params
      params.permit(:field_a, :field_b).to_h.symbolize_keys
    end
  end
end
```

Handler skeleton:

Reference file: `app/services/public_api/end_user_api/v2/digital_download_handler.rb`

```ruby
module PublicApi::EndUserApi::V2
  class <Handler> < PublicApi::V2::BaseHandler
    class << self
      def get_all(school:, user:, filters: {}, page: nil, limit: nil)
        relation = base_relation(school.id, user.id)
        PublicApi::V2::Queries::QueryProcessor.apply(
          relation: relation,
          filters: filters,
          filter_class: PublicApi::V2::Queries::CurrentUser::<Resource>::Filters,
          sorting_class: PublicApi::V2::Queries::CurrentUser::<Resource>::Sorting,
          page: page,
          limit: limit
        )
      end
    end
  end
end
```

Serializer skeleton:

Reference file: `app/serializers/public_api/v2/end_user_purchase_serializer.rb`

```ruby
module PublicApi::EndUserApi::V2
  class <ResourceSerializer> < ActiveModel::Serializer
    attribute :id
    attribute :name
    attribute :href
    attribute :created_at
    attribute :updated_at

    # Use explicit methods for computed/nested fields to keep contract stable.
    attribute :related_resource
    attribute :is_active

    def href
      "/v2/current_user/<resources>/#{object.id}"
    end

    def related_resource
      return nil if object.<association>.blank?

      {
        id: object.<association>.id,
        type: "<type_slug>",
      }
    end

    def is_active
      !!object.is_active
    end
  end
end
```

Query filter skeleton:

Reference file: `app/services/public_api/v2/queries/current_user/digital_downloads/attachments/filters.rb`

```ruby
# app/services/public_api/v2/queries/current_user/<resource>/filters.rb
class PublicApi::V2::Queries::CurrentUser::<Resource>::Filters < PublicApi::V2::Queries::BaseFilters
  def self.filters
    super.merge(
      # equality filter
      <resource_id>: ->(relation, value) {
        relation.where(id: value)
      },

      # text search filter (case-insensitive)
      name: ->(relation, value) {
        relation.where("name ILIKE ?", "%#{value}%")
      },

      # boolean filter
      is_active: ->(relation, value) {
        relation.where(is_active: ActiveModel::Type::Boolean.new.cast(value))
      }
    )
  end
end
```

Sorting skeleton:

Reference file: `app/services/public_api/v2/queries/current_user/digital_downloads/attachments/sorting.rb`

```ruby
# app/services/public_api/v2/queries/current_user/<resource>/sorting.rb
module PublicApi::V2::Queries::CurrentUser::<Resource>
  class Sorting < PublicApi::V2::Queries::Sorting
    ALLOWED_FIELDS = %i[name created_at].freeze
  end
end
```

## 15) Anti-patterns (avoid)

- Fat controller with domain logic.
- Queries/scopes in controller.
- Missing `school` or `user` scoping.
- Returning ad-hoc response envelope different from shared helpers.
- OpenAPI updated without runtime alignment (or vice-versa).
- Missing scope guard.
- Non-deterministic filters/sorting.
