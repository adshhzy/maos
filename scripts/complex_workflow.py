import asyncio
import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow


@dataclass
class OrderInput:
    order_id: str
    customer_id: str
    items: list[str]


NODE_DEFINITIONS = {
    "load_order": {
        "label": "Load order",
        "deps": [],
    },
    "validate_customer": {
        "label": "Validate customer",
        "deps": ["load_order"],
    },
    "reserve_inventory": {
        "label": "Reserve inventory",
        "deps": ["load_order"],
    },
    "calculate_discount": {
        "label": "Calculate discount",
        "deps": ["load_order"],
    },
    "fraud_check": {
        "label": "Fraud check",
        "deps": ["validate_customer", "calculate_discount"],
    },
    "charge_payment": {
        "label": "Charge payment",
        "deps": ["reserve_inventory", "fraud_check"],
    },
    "arrange_shipping": {
        "label": "Arrange shipping",
        "deps": ["charge_payment"],
    },
    "send_notification": {
        "label": "Send notification",
        "deps": ["charge_payment"],
    },
    "close_order": {
        "label": "Close order",
        "deps": ["arrange_shipping", "send_notification"],
    },
}


async def _simulate_work(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    duration_seconds = round(random.uniform(0.5, 4.8), 2)
    activity.logger.info("Running %s for %.2fs", node_name, duration_seconds)
    await asyncio.sleep(duration_seconds)
    return {
        "node": node_name,
        "duration_seconds": duration_seconds,
        "payload": payload,
    }


@activity.defn
async def load_order(order: OrderInput) -> dict[str, Any]:
    return await _simulate_work(
        "load_order",
        {
            "order_id": order.order_id,
            "customer_id": order.customer_id,
            "item_count": len(order.items),
        },
    )


@activity.defn
async def validate_customer(order_snapshot: dict[str, Any]) -> dict[str, Any]:
    return await _simulate_work(
        "validate_customer",
        {
            "customer_id": order_snapshot["payload"]["customer_id"],
            "status": "verified",
        },
    )


@activity.defn
async def reserve_inventory(order_snapshot: dict[str, Any]) -> dict[str, Any]:
    return await _simulate_work(
        "reserve_inventory",
        {
            "order_id": order_snapshot["payload"]["order_id"],
            "reservation_id": f"res-{order_snapshot['payload']['order_id']}",
        },
    )


@activity.defn
async def calculate_discount(order_snapshot: dict[str, Any]) -> dict[str, Any]:
    discount_percent = min(20, order_snapshot["payload"]["item_count"] * 3)
    return await _simulate_work(
        "calculate_discount",
        {
            "order_id": order_snapshot["payload"]["order_id"],
            "discount_percent": discount_percent,
        },
    )


@activity.defn
async def fraud_check(inputs: dict[str, Any]) -> dict[str, Any]:
    return await _simulate_work(
        "fraud_check",
        {
            "customer_status": inputs["customer"]["payload"]["status"],
            "discount_percent": inputs["discount"]["payload"]["discount_percent"],
            "risk": "low",
        },
    )


@activity.defn
async def charge_payment(inputs: dict[str, Any]) -> dict[str, Any]:
    return await _simulate_work(
        "charge_payment",
        {
            "reservation_id": inputs["inventory"]["payload"]["reservation_id"],
            "risk": inputs["fraud"]["payload"]["risk"],
            "payment_status": "captured",
        },
    )


@activity.defn
async def arrange_shipping(payment_result: dict[str, Any]) -> dict[str, Any]:
    return await _simulate_work(
        "arrange_shipping",
        {
            "payment_status": payment_result["payload"]["payment_status"],
            "tracking_number": "TRK-20260610-MAOS",
        },
    )


@activity.defn
async def send_notification(payment_result: dict[str, Any]) -> dict[str, Any]:
    return await _simulate_work(
        "send_notification",
        {
            "payment_status": payment_result["payload"]["payment_status"],
            "channel": "email",
        },
    )


@activity.defn
async def close_order(inputs: dict[str, Any]) -> dict[str, Any]:
    return await _simulate_work(
        "close_order",
        {
            "tracking_number": inputs["shipping"]["payload"]["tracking_number"],
            "notification_channel": inputs["notification"]["payload"]["channel"],
            "order_status": "closed",
        },
    )


ACTIVITIES = [
    load_order,
    validate_customer,
    reserve_inventory,
    calculate_discount,
    fraud_check,
    charge_payment,
    arrange_shipping,
    send_notification,
    close_order,
]


@workflow.defn
class OrderProcessingWorkflow:
    def __init__(self) -> None:
        self._nodes = {
            node_id: {
                "id": node_id,
                "label": definition["label"],
                "deps": definition["deps"],
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
                "summary": "",
            }
            for node_id, definition in NODE_DEFINITIONS.items()
        }
        self._workflow_status = "pending"

    @workflow.run
    async def run(self, order: OrderInput) -> dict[str, Any]:
        self._workflow_status = "running"

        order_snapshot = await self._activity("load_order", load_order, order)

        customer_task = asyncio.create_task(
            self._activity("validate_customer", validate_customer, order_snapshot)
        )
        inventory_task = asyncio.create_task(
            self._activity("reserve_inventory", reserve_inventory, order_snapshot)
        )
        discount_task = asyncio.create_task(
            self._activity("calculate_discount", calculate_discount, order_snapshot)
        )
        customer_result, inventory_result, discount_result = await asyncio.gather(
            customer_task,
            inventory_task,
            discount_task,
        )

        fraud_result = await self._activity(
            "fraud_check",
            fraud_check,
            {
                "customer": customer_result,
                "discount": discount_result,
            },
        )

        payment_result = await self._activity(
            "charge_payment",
            charge_payment,
            {
                "inventory": inventory_result,
                "fraud": fraud_result,
            },
        )

        shipping_task = asyncio.create_task(
            self._activity("arrange_shipping", arrange_shipping, payment_result)
        )
        notification_task = asyncio.create_task(
            self._activity("send_notification", send_notification, payment_result)
        )
        shipping_result, notification_result = await asyncio.gather(
            shipping_task,
            notification_task,
        )

        close_result = await self._activity(
            "close_order",
            close_order,
            {
                "shipping": shipping_result,
                "notification": notification_result,
            },
        )

        self._workflow_status = "completed"
        return {
            "order_id": order.order_id,
            "status": close_result["payload"]["order_status"],
            "tracking_number": shipping_result["payload"]["tracking_number"],
            "node_count": len(self._nodes),
        }

    @workflow.query
    def graph_state(self) -> dict[str, Any]:
        return {
            "workflow_status": self._workflow_status,
            "nodes": list(self._nodes.values()),
        }

    async def _activity(
        self,
        node_id: str,
        activity_fn: Any,
        activity_input: Any,
    ) -> dict[str, Any]:
        node = self._nodes[node_id]
        node["status"] = "running"
        node["started_at"] = workflow.now().isoformat()

        try:
            result = await workflow.execute_activity(
                activity_fn,
                activity_input,
                start_to_close_timeout=timedelta(seconds=8),
            )
        except Exception as exc:
            node["status"] = "failed"
            node["finished_at"] = workflow.now().isoformat()
            node["summary"] = str(exc)
            self._workflow_status = "failed"
            raise

        node["status"] = "completed"
        node["finished_at"] = workflow.now().isoformat()
        node["duration_seconds"] = result["duration_seconds"]
        node["summary"] = _summarize_result(result)
        return result


def _summarize_result(result: dict[str, Any]) -> str:
    payload = result["payload"]
    interesting_keys = [
        "order_status",
        "payment_status",
        "risk",
        "discount_percent",
        "status",
        "tracking_number",
        "channel",
        "reservation_id",
        "item_count",
    ]
    summary_parts = [
        f"{key}={payload[key]}" for key in interesting_keys if key in payload
    ]
    return ", ".join(summary_parts)
