# SmartSurplus
## Software Requirements Document (SRD)

**Project Name:** SmartSurplus  
**Version:** 1.0 (Draft)  
**Document Type:** Software Requirements Document  
**Prepared By:** Arpit Pandey

---

## 1. Purpose

This document defines the functional and non-functional requirements, system architecture, database requirements, interfaces, constraints, and technical specifications of SmartSurplus.

It will serve as a reference for development, testing, deployment, and project evaluation.

---

## 2. System Overview

SmartSurplus is a web application that enables food donors to publish surplus food listings and food receivers to discover and request available donations.

The system uses a web-based frontend, backend application, database, and cloud services.

---

## 3. Proposed Technology Stack

| Component | Technology |
|---|---|
| Frontend | HTML5, CSS3, JavaScript |
| Backend | Python |
| Backend Framework | Flask |
| Database (Current) | SQLite |
| ORM / Database Access | SQLite with Python database connectivity; ORM may be introduced |
| Version Control | Git and GitHub |
| Development Environment | VS Code / Antigravity |
| Cloud Platform | Microsoft Azure |
| Hosting | Azure App Service (proposed) |
| Cloud Database | Azure database service (to be decided) |
| Cloud Storage | Azure Blob Storage (proposed) |
| AI Integration | Azure AI service (optional/proposed) |

---

## 4. System Architecture

The system follows a three-layer architecture.

### 4.1 Presentation Layer

Responsible for user interaction.

Technologies:

- HTML
- CSS
- JavaScript
- Flask templates

Responsibilities:

- Display donation listings.
- Provide registration and login forms.
- Display dashboards.
- Provide search and filtering.
- Display donation details.
- Provide request forms.

### 4.2 Application Layer

Responsible for processing requests and implementing business logic.

Technology:

- Python Flask

Responsibilities:

- Authentication.
- User role management.
- Donation creation and management.
- Search and filtering.
- Donation request processing.
- Status management.
- Input validation.
- Database communication.

### 4.3 Data Layer

Responsible for storing and retrieving information.

Current technology:

- SQLite

Potential cloud deployment:

- Azure-supported database service.

Responsibilities:

- Store users.
- Store donations.
- Store requests.
- Store donation statuses.
- Maintain data consistency.

---

## 5. Functional Requirements

### FR-01: User Registration

The system shall allow users to register using the required information.

The system shall:

- Validate required fields.
- Prevent duplicate accounts based on unique identifiers.
- Store user information securely.
- Assign the appropriate user role.

### FR-02: User Login

The system shall authenticate registered users.

The system shall:

- Validate login credentials.
- Reject invalid credentials.
- Maintain authenticated sessions.
- Restrict protected pages to authenticated users.

### FR-03: Role Management

The system shall distinguish between:

- Donor
- Receiver
- Administrator

The system shall restrict operations based on the user's role.

### FR-04: Create Donation

The system shall allow authenticated donors to create donation listings.

Required information:

- Food name
- Description
- Quantity
- Category
- Location
- Availability/expiry information

The system shall validate donation information before saving it.

### FR-05: View Donations

The system shall allow users to view available food donation listings.

The system shall display donation details, including:

- Food name
- Description
- Quantity
- Category
- Location
- Availability
- Expiry information

### FR-06: Search Donations

The system shall allow users to search donations by keywords.

The search shall support relevant donation fields, such as food name and category.

### FR-07: Filter Donations

The system shall allow users to filter donations according to supported parameters, such as:

- Category
- Location
- Food type
- Availability
- Expiry/urgency

### FR-08: Update Donation

The system shall allow the authorized donor to edit their donation information while the donation is eligible for modification.

### FR-09: Delete or Cancel Donation

The system shall allow authorized donors to remove or cancel their listings when permitted.

The system shall prevent unauthorized users from modifying donations belonging to other users.

### FR-10: Submit Donation Request

The system shall allow receivers to request an available donation.

The system shall:

- Verify that the donation is available.
- Store the request.
- Associate the request with the receiver and donation.
- Prevent invalid requests.

### FR-11: Manage Donation Requests

The system shall allow donors to review incoming requests.

The donor shall be able to accept or reject a request.

The system shall update request status accordingly.

### FR-12: Donation Status

The system shall maintain donation status.

Supported statuses may include:

- Available
- Requested
- Accepted
- Completed
- Cancelled
- Expired

The system shall validate status transitions.

### FR-13: Dashboard

The system shall provide role-specific dashboards.

Donor dashboard shall display donor-related donation information.

Receiver dashboard shall display submitted requests and their statuses.

### FR-14: Administrator Functions

The system shall allow an authorized administrator to:

- View users.
- View donation listings.
- Manage inappropriate listings.
- View donation requests.
- Monitor platform activity.

### FR-15: Notifications

The system may provide notification functionality for relevant system events.

Events include:

- New donation request.
- Request acceptance.
- Request rejection.
- Donation expiry.
- Donation completion.

### FR-16: Azure Integration

The system shall support integration with selected Azure services.

Potential capabilities include:

- Cloud application hosting.
- Cloud storage.
- Database hosting.
- Monitoring.
- AI-assisted functionality, if selected.

---

## 6. Non-Functional Requirements

### NFR-01: Usability

The interface shall be simple, intuitive, and understandable to users with limited technical knowledge.

### NFR-02: Performance

The system should respond to normal user requests within a reasonable time under expected project usage.

Search and filtering should return results without unnecessary delays.

### NFR-03: Security

The system shall:

- Store passwords using secure hashing.
- Validate user inputs.
- Restrict access to protected routes.
- Implement role-based access control.
- Prevent unauthorized donation modification.
- Protect sensitive information.
- Use secure configuration for cloud credentials.

### NFR-04: Reliability

The system should maintain accurate donation and request records.

Database operations should not result in inconsistent donation statuses.

### NFR-05: Availability

The locally hosted application shall be available when the development server is running.

The cloud-hosted version should be accessible through its deployment URL, subject to hosting availability.

### NFR-06: Scalability

The system architecture should allow future migration from a local SQLite database to a managed cloud database.

It should support future increases in users and donation listings.

### NFR-07: Maintainability

The code should be organized into separate components for:

- Routes
- Database operations
- Templates
- Static files
- Authentication
- Business logic
- Configuration

### NFR-08: Compatibility

The application should support modern web browsers.

The interface should be usable on desktop, tablet, and mobile screens.

### NFR-09: Data Integrity

The system shall maintain valid relationships between:

- Users
- Donations
- Requests

The system shall prevent invalid or inconsistent data.

### NFR-10: Privacy

The system shall restrict access to personal information and display only necessary information to users.

---

## 7. Database Requirements

### 7.1 Users Table

| Field | Type | Description |
|---|---|---|
| user_id | Integer | Primary key |
| name | Text | User's name |
| email | Text | Unique email |
| password_hash | Text | Hashed password |
| role | Text | Donor/Receiver/Admin |
| phone | Text | Contact number |
| address | Text | Address/location |
| created_at | Timestamp | Account creation time |

### 7.2 Donations Table

| Field | Type | Description |
|---|---|---|
| donation_id | Integer | Primary key |
| donor_id | Integer | Foreign key to Users |
| food_name | Text | Name of food |
| description | Text | Food description |
| category | Text | Food category |
| quantity | Text/Number | Available quantity |
| food_type | Text | Veg/Non-veg/Other, if applicable |
| pickup_location | Text | Pickup address |
| expiry_time | Timestamp | Expiry/consumption deadline |
| image_path | Text | Optional image location |
| status | Text | Current donation status |
| created_at | Timestamp | Creation date |

### 7.3 Requests Table

| Field | Type | Description |
|---|---|---|
| request_id | Integer | Primary key |
| donation_id | Integer | Foreign key to Donations |
| receiver_id | Integer | Foreign key to Users |
| request_message | Text | Optional message |
| status | Text | Pending/Accepted/Rejected/Completed |
| requested_at | Timestamp | Request date |

### 7.4 Relationships

- One donor can create multiple donations.
- One donation can receive one or more requests, depending on business rules.
- One receiver can create multiple requests.
- Every request is associated with a donation and receiver.

---

## 8. Interface Requirements

### 8.1 Home Page

The home page shall provide:

- Project introduction.
- Navigation.
- Login and registration.
- Available donations.
- Call-to-action buttons.

### 8.2 Registration Page

The registration page shall allow users to enter account information.

### 8.3 Login Page

The login page shall allow users to authenticate.

### 8.4 Donation Form

The donation form shall allow donors to enter donation details.

### 8.5 Donation Listing Page

The listing page shall display available donations.

It shall support search and filtering.

### 8.6 Donation Detail Page

The detail page shall display complete donation information and provide the relevant action.

### 8.7 Dashboard

The dashboard shall display relevant information according to user role.

### 8.8 Admin Interface

The admin interface shall provide administrative management functions.

---

## 9. System Constraints

- The backend shall be developed using Python and Flask.
- The frontend shall use web technologies.
- The existing project uses SQLite during development.
- Azure integration shall use services compatible with the selected implementation.
- Users must have internet access for cloud-hosted features.
- Food donation information depends on user-provided data.
- The system cannot independently guarantee the safety or quality of donated food.

---

## 10. Security Requirements

- Passwords shall not be stored as plain text.
- Authentication shall be required for protected functionality.
- Role authorization shall be implemented.
- Input validation shall be performed.
- Database queries shall avoid SQL injection.
- Secret keys and cloud credentials shall not be committed to GitHub.
- Production deployment shall use HTTPS where supported.
- User sessions shall be managed securely.

---

## 11. Testing Requirements

### Unit Testing

Individual components shall be tested, including:

- Authentication functions.
- Donation creation.
- Search and filtering.
- Request creation.
- Status changes.
- Input validation.

### Integration Testing

The following interactions shall be tested:

- Frontend and Flask backend.
- Flask backend and database.
- User authentication and role management.
- Donation and request functionality.
- Azure services, after integration.

### Functional Testing

Test cases shall verify:

- Successful registration.
- Invalid registration.
- Successful login.
- Invalid login.
- Donation creation.
- Donation listing.
- Donation search.
- Donation filtering.
- Request submission.
- Request acceptance and rejection.
- Status changes.
- Unauthorized access prevention.

### User Acceptance Testing

The system shall be tested to ensure that a donor can list food and a receiver can discover and request it through the application.

---

## 12. Deployment Requirements

The system shall be deployable locally during development.

The proposed cloud deployment shall include:

1. Preparing the application for production.
2. Configuring the required Azure services.
3. Setting environment variables.
4. Configuring the database.
5. Deploying the Flask application.
6. Testing the deployed application.
7. Verifying database operations and cloud functionality.

The final deployment method shall depend on the Azure services selected for the project.

---

## 13. Acceptance Criteria

The software shall be considered acceptable when:

- The application starts without critical errors.
- Users can register and authenticate.
- Users can perform actions according to their roles.
- Donors can create and manage donations.
- Receivers can discover donations.
- Search and filtering work as intended.
- Donation requests can be submitted and managed.
- Database records are stored correctly.
- Unauthorized actions are blocked.
- The application can be tested locally.
- Planned Azure functionality is successfully implemented and tested.

---

## 14. Conclusion

SmartSurplus is a web-based food redistribution application designed to connect surplus food donors with receivers.

The software requirements define the application's functionality, user interaction, database structure, security, testing, and deployment requirements.

The system will be developed incrementally, beginning with core functionality and then integrating cloud services and additional enhancements.
