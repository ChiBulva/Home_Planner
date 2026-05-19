# HouseBoard Development Roadmap

## Project Name

HouseBoard

## Purpose

HouseBoard is a lightweight local-first household operations dashboard designed to run on a Raspberry Pi connected to a permanent household display while also supporting real-time mobile access from phones on the same local network.

The system should support:

* recurring chores
* one-time tasks
* long-term projects
* assignment tracking
* live synchronization
* kiosk dashboard display
* mobile-first task management

---

# Core Technical Stack

## Backend

* Python 3.12
* FastAPI preferred
* SQLAlchemy ORM
* SQLite
* APScheduler
* WebSockets

## Frontend

* HTMX
* TailwindCSS
* Alpine.js (minimal use)

## Infrastructure

* Raspberry Pi Zero 2 W target
* systemd services
* Chromium kiosk mode
* LAN-first architecture

Avoid:

* Docker
* React
* Electron
* Heavy frontend frameworks

---

# High-Level Goals

1. Extremely lightweight
2. Real-time synchronization
3. Works fully offline on LAN
4. Mobile-friendly
5. TV/dashboard-friendly
6. Survives reboot automatically
7. Very low maintenance

---

# Phase 1 — Foundation

## Goal

Create the base application structure and deployment environment.

## Deliverables

### Backend Setup

* Initialize FastAPI project
* Configure virtual environment
* Configure dependency management
* Add environment configuration support

### Database Setup

* Configure SQLite
* Add SQLAlchemy models
* Add migration support

### Frontend Setup

* Configure TailwindCSS
* Configure HTMX
* Create base templates/layouts

### Core Infrastructure

* systemd service files
* auto-start support
* logging setup

---

# Phase 2 — Authentication & Users

## Goal

Basic local authentication system.

## Deliverables

### User System

* Admin role
* User role
* Local login
* Password hashing

### Session Management

* Cookie sessions
* Login persistence
* Logout flow

### User Assignment

Tasks may belong to:

* specific user
* shared

---

# Phase 3 — Task Engine

## Goal

Core task management functionality.

## Deliverables

### Task CRUD

* Create task
* Edit task
* Delete task
* Complete task

### Task Types

* Recurring
* One-time
* Project task

### Task Fields

* title
* description
* assigned user
* due date
* priority
* status
* recurrence rules

---

# Phase 4 — Scheduling Engine

## Goal

Automatic recurring task reset system.

## Deliverables

### Frequency Types

* Daily
* Weekly
* Monthly
* Every X days
* Custom interval
* Specific weekdays
* One-time

### Scheduler

* APScheduler integration
* Automatic next_due calculation
* Automatic overdue detection

### Task Lifecycle

* completion history
* recurring regeneration
* archive support

---

# Phase 5 — Real-Time Sync

## Goal

Instant updates across devices.

## Deliverables

### WebSockets

* live task updates
* live dashboard updates
* multi-device sync

### Events

* task created
* task completed
* task updated
* task deleted

---

# Phase 6 — Dashboard View

## Goal

Permanent household monitor display.

## Deliverables

### Main Dashboard

Sections:

* overdue
* today
* upcoming
* projects
* stats

### Dashboard Requirements

* TV readable
* auto refresh
* responsive
* low CPU usage

### Kiosk Support

* Chromium kiosk startup
* fullscreen mode
* crash recovery

---

# Phase 7 — Mobile Interface

## Goal

Fast mobile task management.

## Deliverables

### Responsive Layout

* phone optimized
* tablet optimized

### Mobile Features

* quick add
* quick complete
* swipe actions (optional)
* filters

### Filters

* mine
* shared
* overdue
* completed
* projects
* recurring

---

# Phase 8 — Projects System

## Goal

Long-term project tracking.

## Deliverables

### Projects

* create project
* project progress
* subtasks
* project status

### Progress Calculation

* based on completed subtasks
* manual override support

---

# Phase 9 — Notifications

## Goal

Optional reminders and alerts.

## Deliverables

### Browser Notifications

* task due
* overdue reminder

### Future Hooks

* Discord
* Email
* SMS

---

# Phase 10 — Deployment Hardening

## Goal

Reliable appliance-style deployment.

## Deliverables

### Reliability

* auto restart
* reboot recovery
* Wi-Fi reconnect handling

### Install Script

Single command installer:

* dependencies
* venv
* migrations
* service setup

### Backup Support

* SQLite backup export
* import/restore support

---

# Database Schema

## users

```sql
id
name
email
password_hash
role
created_at
```

---

## tasks

```sql
id
title
description
type
status
priority
assigned_to
frequency_type
frequency_value
next_due
created_at
updated_at
```

---

## projects

```sql
id
title
description
progress
status
created_at
```

---

## project_tasks

```sql
project_id
task_id
```

---

## task_history

```sql
id
task_id
completed_by
completed_at
```

---

# API Requirements

## REST Endpoints

### Tasks

* GET /tasks
* POST /tasks
* PATCH /tasks/{id}
* DELETE /tasks/{id}

### Projects

* GET /projects
* POST /projects

### Users

* POST /login
* POST /logout
* GET /me

---

# WebSocket Requirements

## Events

### Outbound

* task_created
* task_updated
* task_completed
* task_deleted

### Inbound

* refresh_request

---

# UI Design Requirements

## Design Goals

* glanceable
* minimal
* clean
* readable at distance
* touch friendly

## Inspiration

* Home Assistant
* Todoist
* Trello
* Notion

---

# Performance Targets

## Raspberry Pi Constraints

### Target Hardware

* Raspberry Pi Zero 2 W minimum

### Resource Goals

* under 300MB RAM
* under 10% idle CPU
* under 2 second dashboard load

---

# Suggested Repository Structure

```text
houseboard/
├── app/
│   ├── api/
│   ├── auth/
│   ├── models/
│   ├── services/
│   ├── scheduler/
│   ├── websocket/
│   ├── templates/
│   └── static/
│
├── migrations/
├── scripts/
├── tests/
├── requirements.txt
├── config.py
├── run.py
└── README.md
```

---

# MVP Definition

MVP is complete when:

* tasks can be added from phones
* dashboard updates in real time
* recurring tasks auto-reset
* mobile filters function correctly
* kiosk dashboard runs continuously
* system survives reboot automatically
* fully usable without internet

---

# Stretch Goals

## Future Enhancements

### Smart Home

* Home Assistant integration
* NFC/RFID completion
* voice commands

### AI Features

* smart scheduling
* task prioritization
* workload balancing

### Collaboration

* multiple households
* remote sync
* cloud backup

---

# Expected Deliverables From Developers

* complete source code
* setup documentation
* systemd service files
* SQLite migrations
* API documentation
* deployment instructions
* mobile responsive UI
* LAN deployment support

---

# Success Criteria

The application should feel like:

* a household command center
* an always-on shared planner
* a low-friction productivity appliance

The system must remain:

* stable
* lightweight
* maintainable
* fast
* simple to operate
