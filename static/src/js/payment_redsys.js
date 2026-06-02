import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { register_payment_method } from "@point_of_sale/app/services/pos_store";

export class PaymentRedsys extends PaymentInterface {
    async sendPaymentRequest(cid) {
        const line = this.pos.get_order().get_paymentlines().find((l) => l.cid === cid);
        if (!line) return false;

        line.set_payment_status("waitingCard");

        const result = await rpc("/pos/redsys/pay", {
            payment_method_id: this.payment_method_id.id,
            amount: line.amount,
            invoice_ref: this.pos.get_order().name,
        });

        if (result.authorized) {
            line.set_payment_status("done");
            return true;
        }

        line.set_payment_status("retry");
        this.env.services.notification.add(
            result.error || _t("Pago rechazado por el datáfono"),
            { type: "danger", title: _t("Pago rechazado") }
        );
        return false;
    }

    async sendPaymentCancel(order, cid) {
        await rpc("/pos/redsys/cancel", {
            payment_method_id: this.payment_method_id.id,
        });
        return true;
    }
}

register_payment_method("redsys_tpvpc", PaymentRedsys);
