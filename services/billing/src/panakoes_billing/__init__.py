"""Panakoes Billing microservice.

Handles Stripe-backed subscription lifecycle for the Panakoes product
tiers (Free / Pro / Team). This v0.1 slice is a skeleton: every Stripe
call runs in TEST mode and the wiring to the rest of the platform
(real customer mapping, real product catalog) lands in a follow-up
slice. The shape, contracts, and security paths are stable here.
"""
