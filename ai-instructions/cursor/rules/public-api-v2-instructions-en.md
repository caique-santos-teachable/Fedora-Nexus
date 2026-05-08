# Public API V2 - Development Instructions

## Overview

Public API V2 follows a well-structured and standardized architecture for product endpoints (Courses, Bundles, Coaching, Memberships, Digital Downloads, etc). This document describes the patterns and conventions that should be followed when adding new endpoints or resources.

## Directory Structure

```
app/controllers/public_api/admin_api/v2/
├── application_controller.rb          # Base controller with helpers
├── products_controller.rb             # Unified products endpoint
└── products/
    ├── courses_controller.rb
    ├── bundles_controller.rb
    ├── coaching_controller.rb
    ├── memberships_controller.rb
    ├── digital_downloads_controller.rb
    └── courses/                       # Nested resources of courses
        └── lectures/                  # Nested resources of lectures
            └── quizzes_controller.rb # Quizzes belong to lectures

app/services/public_api/v2/
├── base_handler.rb                    # Base class for all handlers
├── course_handler.rb
├── bundle_handler.rb
├── coaching_handler.rb
├── membership_handler.rb
├── digital_product_handler.rb
├── quiz_handler.rb                    # Handler for quizzes
└── queries/
    └── query_processor.rb

app/serializers/public_api/v2/
├── base_product_serializer.rb         # Base serializer
├── course_serializer.rb
├── bundle_serializer.rb
├── coaching_serializer.rb
├── membership_serializer.rb
└── digital_product_serializer.rb
```

## Architecture Layers

### 1. Controller Layer
- **Location**: `app/controllers/public_api/admin_api/v2/products/`
- **Base Class**: `PublicApi::AdminApi::V2::ApplicationController`
- **Responsibilities**:
  - Validate requests
  - Parse parameters
  - Delegate logic to handlers
  - Render responses with serializers

**Standard Pattern**:
```ruby
module PublicApi::AdminApi::V2::Products
  class CoursesController < PublicApi::AdminApi::V2::ApplicationController
    CourseSerializer = PublicApi::V2::CourseSerializer

    def index
      result = PublicApi::V2::CourseHandler.get_all(
        school: current_school,
        filters: default_filter_params,
        sort_by: params[:sort_by].presence,
        sort_direction: params[:sort_direction].presence,
        page: params[:page],
        limit: params[:per_page]
      )

      values = result.value
      if result.success?
        render json: {
          data: values[:items].map { |item| CourseSerializer.new(item).as_json },
          meta: default_pagination_meta(values),
        }
      else
        render_validation_error(values[:errors])
      end
    end

    def show
      result = PublicApi::V2::CourseHandler.get_by_id(
        id: params[:id],
        school: current_school
      )

      if result.success?
        render json: { data: CourseSerializer.new(result.value).as_json }
      else
        render_not_found_error("Course not found")
      end
    end

    private

    def course_params
      params.permit(:name, :subtitle, :heading, :description, :is_published)
    end
  end
end
```

### 2. Handler Layer (Business Logic)
- **Location**: `app/services/public_api/v2/`
- **Base Class**: `PublicApi::V2::BaseHandler`
- **Responsibilities**:
  - Encapsulate business logic
  - Business rule validations
  - CRUD operations
  - Return Success/Failure monads

**Available Methods in BaseHandler**:
- `get_all(school:, filters:, sort_by:, sort_direction:, page:, limit:)` - Returns paginated items
- `get_by_id(id:, school:)` - Returns a single item
- `create(school:, attributes:)` - Creates new item
- `update(id:, school:, attributes:)` - Updates item
- `destroy(id:, school:)` - Deletes item

**Standard Pattern**:
```ruby
module PublicApi::V2
  class CourseHandler < BaseHandler
    def self.model
      Course
    end

    def self.base_relation(school)
      school.courses.not_destroyed
    end

    def self.allowed_attributes
      [
        :name,
        :heading,
        :description,
        :is_published,
        :author_bio_id,
        :friendly_url,
      ]
    end
  end
end
```

**Required Methods for Subclasses**:
1. `self.model` - Returns the Model class
2. `self.base_relation(school)` - Returns the base relation for retrieving all products (scoped by school or by school_id, whichever is best for performance)
3. `self.allowed_attributes` - Array of attributes permitted for create and update

### 3. Serializer Layer (Response Formatting)
- **Location**: `app/serializers/public_api/v2/`
- **Base Class**: `PublicApi::V2::BaseProductSerializer`
- **Responsibilities**:
  - Format data for JSON response
  - Calculate derived attributes
  - Normalize data types

**Standard Pattern**:
```ruby
module PublicApi::V2
  class CourseSerializer < BaseProductSerializer
    attribute :heading
    attribute :image_url
    attribute :author_name

    def heading
      object.heading
    end

    def image_url
      object.image.url if object.image.attached?
    end

    def author_name
      object.author&.name
    end
  end
end
```

**Attributes Inherited from BaseProductSerializer**:
- `id`
- `name`
- `description`
- `is_published`
- `resource_type` (demodulized and underscore)
- `created_at`
- `updated_at`

## Response Patterns

### Success - Collection (Index)
```json
{
  "data": [
    {
      "id": 1,
      "name": "Course Name",
      "description": "...",
      "is_published": true,
      "created_at": "2025-02-20T10:00:00Z",
      "updated_at": "2025-02-20T10:00:00Z"
    }
  ],
  "meta": {
    "page": 1,
    "per_page": 25,
    "total_pages": 4,
    "total_count": 100
  }
}
```

### Success - Single Resource (Show)
```json
{
  "data": {
    "id": 1,
    "name": "Course Name",
    "description": "...",
    "type": "course",
    "is_published": true,
    "created_at": "2025-02-20T10:00:00Z",
    "updated_at": "2025-02-20T10:00:00Z"
  }
}
```

### Error - Validation (422)
```json
{
  "error": {
    "code": "validation_failed",
    "message": "The request could not be processed",
    "details": {
      "name": ["can't be blank"],
      "description": ["is too short"]
    }
  }
}
```

### Error - Not Found (404)
```json
{
  "error": {
    "code": "not_found",
    "message": "Resource not found"
  }
}
```

## Routes

Routes follow the RESTful pattern within the `namespace :products`:

```ruby
# config/routes.rb
namespace :admin_api do
  namespace :v2 do
    get "products", to: "products#index"

    namespace :products do
      resources :courses, only: [:index, :show]
      resources :bundles, only: [:index, :show]
      resources :coaching, only: [:index, :show]
      resources :memberships, only: [:index, :show]
      resources :digital_downloads, only: [:index, :show]
    end
  end
end
```

**Available Endpoints**:
- `GET /v2/products/courses` - List courses
- `GET /v2/products/courses/:id` - Course detail
- `GET /v2/products/bundles` - List bundles
- `GET /v2/products/bundles/:id` - Bundle detail
- And so on for each product type

## Adding New Resources to an Existing Product

If you need to add nested endpoints (ex: quizzes within lectures), follow this pattern:

### 1. Create folder structure that reflects the resource hierarchy

**IMPORTANT**: The folder structure should reflect the resource hierarchy. For example, if `quizzes` belong to `lectures`, and `lectures` belong to `courses`:

```
app/controllers/public_api/admin_api/v2/products/courses/
└── lectures/                          # Folder for lectures resources
    └── quizzes_controller.rb         # Quizzes belong to lectures
```

### 2. Create nested controller with correct namespace

**IMPORTANT**: The namespace should correspond to the folder structure.

```ruby
module PublicApi::AdminApi::V2::Products::Courses::Lectures
  class QuizzesController < PublicApi::AdminApi::V2::ApplicationController
    QuizSerializer = PublicApi::V2::QuizSerializer

    def index
      course_id = params[:course_id]
      lecture_id = params[:lecture_id]

      result = PublicApi::V2::QuizHandler.get_all(
        school: current_school,
        course_id: course_id,
        lecture_id: lecture_id,
        filters: default_filter_params,
        page: params[:page],
        limit: params[:per_page]
      )

      values = result.value
      if result.success?
        render json: {
          data: values[:items].map { |item| QuizSerializer.new(item).as_json },
          meta: default_pagination_meta(values),
        }
      else
        render_validation_error(values[:errors])
      end
    end
  end
end
```

### 3. Create handler with support for parent parameters
```ruby
module PublicApi::V2
  class QuizHandler < BaseHandler
    def self.model
      Quiz
    end

    def self.base_relation(school, course_id:, lecture_id:)
      course = school.courses.find(course_id)
      lecture = course.lectures.find(lecture_id)
      lecture.quizzes
    end

    def self.allowed_attributes
      [:name, :description, :is_published]
    end
  end
end
```

### 4. Add nested route
```ruby
namespace :products do
  resources :courses, only: [:index, :show] do
    resources :lectures, only: [:index, :show] do
      resources :quizzes, only: [:index, :show]
    end
  end
end
```

### 5. Create serializer
```ruby
module PublicApi::V2
  class QuizSerializer < BaseProductSerializer
    attribute :question_count

    def question_count
      object.questions.count
    end
  end
end
```

## Multi-Tenancy

All endpoints respect multi-tenancy through the inclusion of `ScopedToSchool` in models:

- All handlers should have school filtering, when possible, either by the school model or by school_id (when the model has this column to make the query more performant and avoid unnecessary joins)
- The `base_relation` always filters by school or by the parent resource, for security reasons

```ruby
# ✅ Correct
def self.base_relation(school)
  school.courses.not_destroyed
end

# ❌ Incorrect
def self.base_relation(school)
  Course.not_destroyed
end
```

## Error Handling

Use the helpers available in `ApplicationController`:

```ruby
# Validation error
render_validation_error(model_instance.errors)

# Resource not found
render_not_found_error("Course not found")

# Success - returns Success/Failure monad
result = PublicApi::V2::CourseHandler.get_all(...)
if result.success?
  # Handle success
else
  # Handle failure
end
```

## Filters and Pagination

Handlers use `Queries::QueryProcessor` to apply filters:

**Available filters**:
- `filters`: Hash with custom conditions
- `sort_by`: Field for sorting
- `sort_direction`: 'asc' or 'desc'
- `page`: Page number (default: 1)
- `limit`: Items per page (default: 25)

```ruby
result = PublicApi::V2::CourseHandler.get_all(
  school: current_school,
  filters: { is_published: true },
  sort_by: 'created_at',
  sort_direction: 'desc',
  page: 2,
  limit: 50
)
```

## Best Practices

1. **Always scope by school**: Every query should include `school_id`
2. **Use the Monad pattern**: Success/Failure for operations
3. **Validate in multiple layers**: Controller (params), Handler (rules), Model (constraints)
4. **Reuse serializers**: Don't duplicate formatting logic
5. **Document allowed attributes**: In `allowed_attributes`
6. **Use descriptive names**: Controllers, handlers and serializers with clear names
7. **Handle errors gracefully**: Return useful messages to the client
8. **Maintain response consistency**: Always `data` + `meta` for collections
9. The Query Processor and all related query and model files must be defined and used only inside handlers, as handlers are responsible for acting as services or use cases.

## Adding New Product Types

If you need to add a new product type (ex: Webinars):

1. Create `WebinarHandler` in `app/services/public_api/v2/webinar_handler.rb`
2. Create `WebinarSerializer` in `app/serializers/public_api/v2/webinar_serializer.rb`
3. Create `WebinarsController` in `app/controllers/public_api/admin_api/v2/products/webinars_controller.rb`
4. Add route in `config/routes.rb` within `namespace :products`

## Testing

Test pattern follows the structure:
- Controller tests in `spec/controllers/public_api/admin_api/v2/products/`
- Handler tests in `spec/services/public_api/v2/`
- Serializer tests in `spec/serializers/public_api/v2/`

Use Fabricators to create test data:
```ruby
# spec/fabricators/course_fabricator.rb
Fabricator(:course) do
  school
  name { Faker::Lorem.words(3).join(' ') }
  is_published false
end
```

## References

- **BaseHandler**: `app/services/public_api/v2/base_handler.rb`
- **ApplicationController**: `app/controllers/public_api/admin_api/v2/application_controller.rb`
- **BaseProductSerializer**: `app/serializers/public_api/v2/base_product_serializer.rb`
- **Handler Examples**: `app/services/public_api/v2/{course,bundle,coaching,membership,digital_product}_handler.rb`
- **Controller Examples**: `app/controllers/public_api/admin_api/v2/products/{courses,bundles,coaching,memberships,digital_downloads}_controller.rb`
