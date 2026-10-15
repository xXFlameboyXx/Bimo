# Development Prompts for Quick Delivery App (Blinkit Clone)

Use these prompts sequentially with your AI coding assistant to build your quick commerce delivery application from scratch.

---

## Phase 1: Tech Stack & System Architecture Blueprint

**AI Prompt:**
> "Act as a Lead Systems Architect. Design the overall architecture for a 10-minute quick-commerce delivery application similar to Blinkit. Provide a complete tech stack recommendation for:
> 1. Mobile Frontend (React Native or Flutter for iOS & Android)
> 2. Backend Services (Node.js/Express, Python/FastAPI, or Go)
> 3. Database (PostgreSQL for relational data, Redis for caching/live locations, MongoDB for flexible catalogs)
> 4. Real-time Communication (WebSockets or Socket.io for live order and GPS tracking)
> 5. Cloud Infrastructure & Payment Integration
> Include a high-level system diagram and project file directory structure."

---

## Phase 2: Database Schema & User Authentication

**AI Prompt:**
> "Design a database schema using PostgreSQL and Prisma ORM for a grocery delivery app. Create migration scripts and models for:
> 1. Users (Customers, Delivery Partners, Store Managers, Admins) with role-based access control (RBAC).
> 2. Dark Stores / Warehouses (location, address, operating hours, service radius).
> 3. Products & Categories (SKU, title, price, discount, inventory level per store, unit weight).
> 4. Orders & Order Items (status breakdown: Pending, Assigned, Packing, Out for Delivery, Delivered, Cancelled).
> 5. Delivery Tracking & Payments.
> Next, write an authentication module using JWT and OTP phone verification for user signup and login."

---

## Phase 3: Store Inventory & Dark Store Management Backend

**AI Prompt:**
> "Build a RESTful API backend service using Node.js and Express for managing dark store inventory:
> 1. Create CRUD endpoints for products, inventory levels, and stock allocations per dark store location.
> 2. Implement geo-fencing logic using PostgreSQL PostGIS or H3 spatial index to automatically map a customer's location to the nearest operational dark store within a 3km radius.
> 3. Add inventory locks so that items are reserved during active checkout for 10 minutes."

---

## Phase 4: Customer Mobile App (UI & Core Features)

**AI Prompt:**
> "Build a modern cross-platform mobile app interface using React Native / Flutter for the customer-facing delivery app. Include:
> 1. Home Screen with dynamic location picker, search bar, banner carousel, and category grids.
> 2. Fast Product Catalog & Search with debounced auto-complete, filters, and quick 'Add to Cart' buttons with quantity increments.
> 3. Cart & Checkout Screen displaying delivery fee breakdown, estimated arrival time (10-15 mins), coupon application, and address selection.
> 4. Use clean state management (Redux Toolkit or Zustand) and sleek UI components."

---

## Phase 5: Real-Time Order Processing & Dispatch Engine

**AI Prompt:**
> "Create a real-time order processing microservice using Node.js, Socket.io, and Redis BullMQ queue:
> 1. Handle order placement, payment confirmation webhook, and push notifications to nearby available delivery drivers.
> 2. Build an automated driver assignment algorithm based on proximity to dark store and driver availability.
> 3. Implement WebSocket events for real-time order status updates: Order Placed -> Store Packing -> Driver Assigned -> Picked Up -> Out for Delivery -> Delivered."

---

## Phase 6: Delivery Partner (Driver) Mobile App

**AI Prompt:**
> "Develop a dedicated Mobile App interface for Delivery Partners using React Native / Flutter:
> 1. Real-time Order Request Screen with dynamic pop-up to accept or reject delivery assignments within 30 seconds.
> 2. Order Fulfillment Workflow showing Dark Store pickup address, order item checklist, and customer delivery location.
> 3. Live GPS Location Streaming sending location updates every 5 seconds to the backend via WebSockets while on an active order.
> 4. Earnings & Payout Dashboard showing daily completed orders, tips, and incentive progress."

---

## Phase 7: Live Map Tracking & Route Navigation

**AI Prompt:**
> "Implement real-time order tracking for the Customer App:
> 1. Integrate Google Maps API / Mapbox SDK to render interactive maps.
> 2. Draw optimized route polylines between dark store, driver live position, and customer address.
> 3. Animate the delivery vehicle icon seamlessly based on incoming WebSocket coordinate updates from the driver app.
> 4. Display dynamic Estimated Time of Arrival (ETA) calculated using live distance matrix."

---

## Phase 8: Payment Gateway & Push Notifications

**AI Prompt:**
> "Integrate payment services and notification alerts:
> 1. Implement Razorpay / Stripe payment gateway for UPI, credit cards, debit cards, and net banking with webhook handling for payment success/failure.
> 2. Set up Firebase Cloud Messaging (FCM) to trigger instant push notifications for order confirmation, driver assignment, out-for-delivery, and arrival alerts.
> 3. Create SMS alerts for login OTPs and order status updates using Twilio or AWS SNS."

---

## Phase 9: Admin Dashboard & Operations Panel

**AI Prompt:**
> "Build a web-based Admin & Operations Dashboard using Next.js, React, and Tailwind CSS:
> 1. Live Order Monitoring Map displaying active deliveries, store status, and driver locations.
> 2. Store Manager Panel to view incoming orders, mark items out of stock, and manage packing queues.
> 3. Analytics Dashboard displaying total sales, average delivery times, driver efficiency, and revenue metrics."

---

## Phase 10: Testing, Security & Production Deployment

**AI Prompt:**
> "Write deployment and testing scripts for the entire delivery app stack:
> 1. Unit and integration tests for backend API routes and order status state machines.
> 2. Docker compose configuration for running PostgreSQL, Redis, and Node.js microservices locally.
> 3. Production setup blueprint using AWS (EC2/ECS, S3, RDS PostgreSQL) or DigitalOcean with Nginx reverse proxy, SSL certificates, and CI/CD pipelines using GitHub Actions."
