# Copyright 2017-2018  Alexandre Díaz
# Copyright 2017  Dario Lodeiros
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from datetime import datetime
import time
import pytz
import logging
from decimal import Decimal
from dateutil.relativedelta import relativedelta
from odoo.exceptions import except_orm, UserError, ValidationError
from odoo.tools import (
    misc,
    DEFAULT_SERVER_DATETIME_FORMAT,
    DEFAULT_SERVER_DATE_FORMAT)
from odoo import models, fields, api, _
_logger = logging.getLogger(__name__)

from odoo.addons import decimal_precision as dp


class HotelFolio(models.Model):
    _name = 'hotel.folio'
    _description = 'Hotel Folio'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'portal.mixin']
    _order = 'id'

    # @api.depends('invoice_lines.invoice_id.state', 'invoice_lines.quantity')
    def _get_invoice_qty(self):
        pass
    # @api.depends('product_id.invoice_policy', 'order_id.state')
    def _compute_qty_delivered_updateable(self):
        pass
    # @api.depends('state', 'order_line.invoice_status')
    def _get_invoiced(self):
        pass
    # @api.depends('state', 'product_uom_qty', 'qty_delivered', 'qty_to_invoice', 'qty_invoiced')
    def _compute_invoice_status(self):
        pass
    # @api.depends('product_uom_qty', 'discount', 'price_unit', 'tax_id')
    def _compute_amount(self):
        pass
    # @api.depends('order_line.price_total')
    def _amount_all(self):
        pass

    #Main Fields--------------------------------------------------------
    name = fields.Char('Folio Number', readonly=True, index=True,
                       default=lambda self: _('New'))
    partner_id = fields.Many2one('res.partner',
                                 track_visibility='onchange')

    room_lines = fields.One2many('hotel.reservation', 'folio_id',
                                 readonly=False,
                                 states={'done': [('readonly', True)]},
                                 help="Hotel room reservation detail.",)

    service_ids = fields.One2many('hotel.service', 'folio_id',
                                       readonly=False,
                                       states={'done': [('readonly', True)]},
                                       help="Hotel services detail provide to "
                                       "customer and it will include in "
                                       "main Invoice.")
    company_id = fields.Many2one('res.company', 'Company')

    currency_id = fields.Many2one('res.currency', related='pricelist_id.currency_id',
                                  string='Currency', readonly=True, required=True)

    pricelist_id = fields.Many2one('product.pricelist',
                                   string='Pricelist',
                                   required=True,
                                   states={'draft': [('readonly', False)],
                                           'sent': [('readonly', False)]},
                                   help="Pricelist for current folio.")
    reservation_type = fields.Selection([('normal', 'Normal'),
                                         ('staff', 'Staff'),
                                         ('out', 'Out of Service')],
                                        'Type', default=lambda *a: 'normal')
    channel_type = fields.Selection([('door', 'Door'),
                                     ('mail', 'Mail'),
                                     ('phone', 'Phone'),
                                     ('web', 'Web')], 'Sales Channel', default='door')
    user_id = fields.Many2one('res.users', string='Salesperson', index=True,
                              track_visibility='onchange', default=lambda self: self.env.user)
    date_order = fields.Datetime(
        string='Order Date',
        required=True, readonly=True, index=True,
        states={'draft': [('readonly', False)], 'sent': [('readonly', False)]},
        copy=False, default=fields.Datetime.now)
    state = fields.Selection([
        ('draft', 'Quotation'),
        ('sent', 'Quotation Sent'),
        ('confirm', 'Confirmed'),
        ('done', 'Locked'),
        ('cancel', 'Cancelled'),
        ], string='Status',
        readonly=True, copy=False,
        index=True, track_visibility='onchange',
        default='draft')
    

    # Partner fields for being used directly in the Folio views---------
    email = fields.Char('E-mail', related='partner_id.email')
    mobile = fields.Char('Mobile', related='partner_id.mobile')
    phone = fields.Char('Phone', related='partner_id.phone')
    partner_internal_comment = fields.Text(string='Internal Partner Notes',
                                           related='partner_id.comment')

    #Payment Fields-----------------------------------------------------
    payment_ids = fields.One2many('account.payment', 'folio_id',
                                  readonly=True)
    return_ids = fields.One2many('payment.return', 'folio_id',
                                 readonly=True)

    #Amount Fields------------------------------------------------------
    pending_amount = fields.Monetary(compute='compute_amount',
                                     store=True,
                                     string="Pending in Folio")
    refund_amount = fields.Monetary(compute='compute_amount',
                                    store=True,
                                    string="Payment Returns")
    invoices_paid = fields.Monetary(compute='compute_amount',
                                    store=True, track_visibility='onchange',
                                    string="Payments")
    amount_untaxed = fields.Monetary(string='Untaxed Amount', store=True,
                                     readonly=True, compute='_amount_all',
                                     track_visibility='onchange')
    amount_tax = fields.Monetary(string='Taxes', store=True,
                                 readonly=True, compute='_amount_all')
    amount_total = fields.Monetary(string='Total', store=True, readonly=True,
                                   compute='_amount_all', track_visibility='always')

    #Checkin Fields-----------------------------------------------------
    booking_pending = fields.Integer('Booking pending',
                                     compute='_compute_checkin_partner_count')
    checkin_partner_count = fields.Integer('Checkin counter',
                                  compute='_compute_checkin_partner_count')
    checkin_partner_pending_count = fields.Integer('Checkin Pending',
                                          compute='_compute_checkin_partner_count')

    #Invoice Fields-----------------------------------------------------
    hotel_invoice_id = fields.Many2one('account.invoice', 'Invoice')
    num_invoices = fields.Integer(compute='_compute_num_invoices')
    invoice_ids = fields.Many2many('account.invoice', string='Invoices',
                                   compute='_get_invoiced', readonly=True, copy=False)
    invoice_status = fields.Selection([('upselling', 'Upselling Opportunity'),
                                       ('invoiced', 'Fully Invoiced'),
                                       ('to invoice', 'To Invoice'),
                                       ('no', 'Nothing to Invoice')],
                                      string='Invoice Status',
                                      compute='_compute_invoice_status',
                                      store=True, readonly=True, default='no')
    #~ partner_invoice_id = fields.Many2one('res.partner',
                                         #~ string='Invoice Address',
                                         #~ readonly=True, required=True,
                                         #~ states={'draft': [('readonly', False)],
                                                 #~ 'sent': [('readonly', False)]},
                                         #~ help="Invoice address for current sales order.")

    #WorkFlow Mail Fields-----------------------------------------------
    has_confirmed_reservations_to_send = fields.Boolean(
        compute='_compute_has_confirmed_reservations_to_send')
    has_cancelled_reservations_to_send = fields.Boolean(
        compute='_compute_has_cancelled_reservations_to_send')
    has_checkout_to_send = fields.Boolean(
        compute='_compute_has_checkout_to_send')

    #Generic Fields-----------------------------------------------------    
    internal_comment = fields.Text(string='Internal Folio Notes')
    cancelled_reason = fields.Text('Cause of cancelled')
    closure_reason_id = fields.Many2one('room.closure.reason')
    prepaid_warning_days = fields.Integer(
        'Prepaid Warning Days',
        help='Margin in days to create a notice if a payment \
                advance has not been recorded')
    rooms_char = fields.Char('Rooms', compute='_computed_rooms_char')
    segmentation_ids = fields.Many2many('res.partner.category',
                                        string='Segmentation')
    client_order_ref = fields.Char(string='Customer Reference', copy=False)
    note = fields.Text('Terms and conditions')
    sequence = fields.Integer(string='Sequence', default=10)

    @api.depends('room_lines.price_total','service_ids.price_total')
    def _amount_all(self):
        """
        Compute the total amounts of the SO.
        """
        for record in self:
            amount_untaxed = amount_tax = 0.0
            amount_untaxed = sum(record.room_lines.mapped('price_subtotal')) + \
                             sum(record.service_ids.mapped('price_subtotal'))
            amount_tax = sum(record.room_lines.mapped('price_tax')) + \
                         sum(record.service_ids.mapped('price_tax'))
            record.update({
                'amount_untaxed': record.pricelist_id.currency_id.round(amount_untaxed),
                'amount_tax': record.pricelist_id.currency_id.round(amount_tax),
                'amount_total': amount_untaxed + amount_tax,
            })

    def _computed_rooms_char(self):
        for record in self:
            record.rooms_char = ', '.join(record.mapped('room_lines.room_id.name'))

    @api.multi
    def _compute_num_invoices(self):
        pass
        # for fol in self:
        #     fol.num_invoices =  len(self.mapped('invoice_ids.id'))

    # @api.depends('order_line.price_total', 'payment_ids', 'return_ids')
    @api.multi
    def compute_amount(self):
        _logger.info('compute_amount')

    @api.multi
    def action_pay(self):
        self.ensure_one()
        partner = self.partner_id.id
        amount = self.pending_amount
        view_id = self.env.ref('hotel.account_payment_view_form_folio').id
        return{
            'name': _('Register Payment'),
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'account.payment',
            'type': 'ir.actions.act_window',
            'view_id': view_id,
            'context': {
                'default_folio_id': self.id,
                'default_amount': amount,
                'default_payment_type': 'inbound',
                'default_partner_type': 'customer',
                'default_partner_id': partner,
                'default_communication': self.name,
            },
            'target': 'new',
        }

    @api.multi
    def action_payments(self):
        self.ensure_one()
        payments_obj = self.env['account.payment']
        payments = payments_obj.search([('folio_id', '=', self.id)])
        #invoices = self.mapped('invoice_ids.id')
        return{
            'name': _('Payments'),
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': 'account.payment',
            'target': 'new',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', payments.ids)],
        }

    @api.multi
    def open_invoices_folio(self):
        invoices = self.mapped('invoice_ids')
        action = self.env.ref('account.action_invoice_tree1').read()[0]
        if len(invoices) > 1:
            action['domain'] = [('id', 'in', invoices.ids)]
        elif len(invoices) == 1:
            action['views'] = [(self.env.ref('account.invoice_form').id, 'form')]
            action['res_id'] = invoices.ids[0]
        else:
            action = {'type': 'ir.actions.act_window_close'}
        return action

    @api.multi
    def action_return_payments(self):
        self.ensure_one()
        return_move_ids = []
        acc_pay_obj = self.env['account.payment']
        payments = acc_pay_obj.search([
            '|',
            ('invoice_ids', 'in', self.invoice_ids.ids),
            ('folio_id', '=', self.id)
        ])
        return_move_ids += self.invoice_ids.filtered(
            lambda invoice: invoice.type == 'out_refund').mapped(
                'payment_move_line_ids.move_id.id')
        return_lines = self.env['payment.return.line'].search([
            ('move_line_ids', 'in', payments.mapped('move_line_ids.id')),
        ])
        return_move_ids += return_lines.mapped('return_id.move_id.id')

        return{
            'name': _('Returns'),
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': 'account.move',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', return_move_ids)],
        }

    @api.multi
    def action_folios_amount(self):
        reservations = self.env['hotel.reservation'].search([
            ('checkout', '<=', fields.Date.today())
        ])
        folio_ids = reservations.mapped('folio_id.id')
        folios = self.env['hotel.folio'].search([('id', 'in', folio_ids)])
        folios = folios.filtered(lambda r: r.pending_amount > 0)
        return {
            'name': _('Pending'),
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': 'hotel.folio',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', folios.ids)]
        }

    @api.multi
    def go_to_currency_exchange(self):
        '''
         when Money Exchange button is clicked then this method is called.
        -------------------------------------------------------------------
        @param self: object pointer
        '''
        _logger.info('go_to_currency_exchange')
        pass
        # cr, uid, context = self.env.args
        # context = dict(context)
        # for rec in self:
        #     if rec.partner_id.id and len(rec.room_lines) != 0:
        #         context.update({'folioid': rec.id, 'guest': rec.partner_id.id,
        #                         'room_no': rec.room_lines[0].product_id.name})
        #         self.env.args = cr, uid, misc.frozendict(context)
        #     else:
        #         raise except_orm(_('Warning'), _('Please Reserve Any Room.'))
        # return {'name': _('Currency Exchange'),
        #         'res_model': 'currency.exchange',
        #         'type': 'ir.actions.act_window',
        #         'view_id': False,
        #         'view_mode': 'form,tree',
        #         'view_type': 'form',
        #         'context': {'default_folio_no': context.get('folioid'),
        #                     'default_hotel_id': context.get('hotel'),
        #                     'default_guest_name': context.get('guest'),
        #                     'default_room_number': context.get('room_no')
        #                     },
        #         }

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New') or 'name' not in vals:
            if 'company_id' in vals:
                vals['name'] = self.env['ir.sequence'].with_context(
                    force_company=vals['company_id']
                ).next_by_code('hotel.folio') or _('New')
            else:
                vals['name'] = self.env['ir.sequence'].next_by_code('hotel.folio') or _('New')
        

        # Makes sure partner_invoice_id' and 'pricelist_id' are defined
        lfields = ('partner_invoice_id', 'partner_shipping_id', 'pricelist_id')
        if any(f not in vals for f in lfields):
            partner = self.env['res.partner'].browse(vals.get('partner_id'))
            addr = partner.address_get(['delivery', 'invoice'])
            #~ vals['partner_invoice_id'] = vals.setdefault('partner_invoice_id', addr['invoice'])
            vals['pricelist_id'] = vals.setdefault(
                'pricelist_id',
                partner.property_product_pricelist and partner.property_product_pricelist.id)
        result = super(HotelFolio, self).create(vals)
        return result

    @api.multi
    @api.onchange('partner_id')
    def onchange_partner_id(self):
        """
        Update the following fields when the partner is changed:
        - Pricelist
        - Invoice address
        - user_id
        """
        if not self.partner_id:
            #~ self.update({
                #~ 'partner_invoice_id': False,
                #~ 'payment_term_id': False,
                #~ 'fiscal_position_id': False,
            #~ })
            return
        addr = self.partner_id.address_get(['invoice'])
        pricelist = self.partner_id.property_product_pricelist and \
                                 self.partner_id.property_product_pricelist.id or \
                                 self.env['ir.default'].sudo().get('res.config.settings', 'default_pricelist_id')
        values = {'user_id': self.partner_id.user_id.id or self.env.uid,
                  'pricelist_id': pricelist
                  }
        if self.env['ir.config_parameter'].sudo().get_param('sale.use_sale_note') and \
            self.env.user.company_id.sale_note:
            values['note'] = self.with_context(
                lang=self.partner_id.lang).env.user.company_id.sale_note

        if self.partner_id.team_id:
            values['team_id'] = self.partner_id.team_id.id
        self.update(values)

    @api.multi
    @api.onchange('pricelist_id')
    def onchange_pricelist_id(self):
        values = {'reservation_type': self.env['hotel.folio'].calcule_reservation_type(
                                       self.pricelist_id.is_staff,
                                       self.reservation_type)}
        self.update(values)
    

    @api.model
    def calcule_reservation_type(self, is_staff, current_type):
        if current_type == 'out':
            return 'out'
        elif is_staff:
            return 'staff'
        else:
            return 'normal'

    @api.multi
    def action_invoice_create(self, grouped=False, states=None):
        '''
        @param self: object pointer
        '''
        pass
        # if states is None:
        #     states = ['confirmed', 'done']
        # order_ids = [folio.order_id.id for folio in self]
        # sale_obj = self.env['sale.order'].browse(order_ids)
        # invoice_id = (sale_obj.action_invoice_create(grouped=False,
        #                                              states=['confirmed',
        #                                                      'done']))
        # for line in self:
        #     values = {'invoiced': True,
        #               'state': 'progress' if grouped else 'progress',
        #               'hotel_invoice_id': invoice_id
        #               }
        #     line.write(values)
        # return invoice_id

    @api.multi
    def advance_invoice(self):
        pass

    '''
    WORKFLOW STATE
    '''

    @api.multi
    def button_dummy(self):
        '''
        @param self: object pointer
        '''
        # for folio in self:
        #     folio.order_id.button_dummy()
        return True

    @api.multi
    def action_done(self):
        room_lines = self.mapped('room_lines')
        for line in room_lines:
            if line.state == "booking":
                line.action_reservation_checkout()

    @api.multi
    def action_cancel(self):
        '''
        @param self: object pointer
        '''
        pass
        # for sale in self:
        #     if not sale.order_id:
        #         raise ValidationError(_('Order id is not available'))
        #     for invoice in sale.invoice_ids:
        #         invoice.state = 'cancel'
        #     sale.room_lines.action_cancel()
        #     sale.order_id.action_cancel()

    @api.multi
    def print_quotation(self):
        pass
        # TODO- New report to reservation order
        # self.order_id.filtered(lambda s: s.state == 'draft').write({
        #     'state': 'sent',
        # })
        # return self.env.ref('sale.report_saleorder').report_action(self, data=data)

    @api.multi
    def action_confirm(self):
        _logger.info('action_confirm')


    """
    CHECKIN/OUT PROCESS
    """
    @api.multi
    def action_checks(self):
        self.ensure_one()
        rooms = self.mapped('room_lines.id')
        return {
            'name': _('Checkins'),
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': 'hotel.checkin.partner',
            'type': 'ir.actions.act_window',
            'domain': [('reservation_id', 'in', rooms)],
            'target': 'new',
        }

    @api.multi
    def _compute_checkin_partner_count(self):
        _logger.info('_compute_checkin_partner_amount')
        for record in self:
            if record.reservation_type == 'normal' and record.room_lines:
                write_vals = {}
                filtered_reservs = record.room_lines.filtered(
                    lambda x: x.state != 'cancelled' and \
                        not x.parent_reservation)
                mapped_checkin_partner = filtered_reservs.mapped('checkin_partner_ids.id')
                record.checkin_partner_count = len(mapped_checkin_partner)
                mapped_checkin_partner_count = filtered_reservs.mapped(
                    lambda x: (x.adults + x.children) - len(x.checkin_partner_ids))
                record.checkin_partner_pending_count = sum(mapped_checkin_partner_count)

    """
    MAILING PROCESS
    """

    @api.depends('room_lines')
    def _compute_has_confirmed_reservations_to_send(self):
        has_to_send = False
        for rline in self.room_lines:
            if rline.splitted:
                master_reservation = rline.parent_reservation or rline
                has_to_send = self.env['hotel.reservation'].search_count([
                    ('splitted', '=', True),
                    ('folio_id', '=', self.id),
                    ('to_send', '=', True),
                    ('state', 'in', ('confirm', 'booking')),
                    '|',
                    ('parent_reservation', '=', master_reservation.id),
                    ('id', '=', master_reservation.id),
                ]) > 0
            elif rline.to_send and rline.state in ('confirm', 'booking'):
                has_to_send = True
                break
        self.has_confirmed_reservations_to_send = has_to_send

    @api.depends('room_lines')
    def _compute_has_cancelled_reservations_to_send(self):
        has_to_send = False
        for rline in self.room_lines:
            if rline.splitted:
                master_reservation = rline.parent_reservation or rline
                has_to_send = self.env['hotel.reservation'].search_count([
                    ('splitted', '=', True),
                    ('folio_id', '=', self.id),
                    ('to_send', '=', True),
                    ('state', '=', 'cancelled'),
                    '|',
                    ('parent_reservation', '=', master_reservation.id),
                    ('id', '=', master_reservation.id),
                ]) > 0
            elif rline.to_send and rline.state == 'cancelled':
                has_to_send = True
                break
        self.has_cancelled_reservations_to_send = has_to_send

    @api.depends('room_lines')
    def _compute_has_checkout_to_send(self):
        has_to_send = True
        for rline in self.room_lines:
            if rline.splitted:
                master_reservation = rline.parent_reservation or rline
                nreservs = self.env['hotel.reservation'].search_count([
                    ('splitted', '=', True),
                    ('folio_id', '=', self.id),
                    ('to_send', '=', True),
                    ('state', '=', 'done'),
                    '|',
                    ('parent_reservation', '=', master_reservation.id),
                    ('id', '=', master_reservation.id),
                ])
                if nreservs != len(self.room_lines):
                    has_to_send = False
            elif not rline.to_send or rline.state != 'done':
                has_to_send = False
                break
        self.has_checkout_to_send = has_to_send

    @api.multi
    def send_reservation_mail(self):
        '''
        This function opens a window to compose an email,
        template message loaded by default.
        @param self: object pointer
        '''
        # Debug Stop -------------------
        # import wdb; wdb.set_trace()
        # Debug Stop -------------------
        self.ensure_one()
        ir_model_data = self.env['ir.model.data']
        try:
            template_id = ir_model_data.get_object_reference(
                'hotel',
                'mail_template_hotel_reservation')[1]
        except ValueError:
            template_id = False
        try:
            compose_form_id = ir_model_data.get_object_reference(
                'mail',
                'email_compose_message_wizard_form')[1]
        except ValueError:
            compose_form_id = False
        ctx = dict()
        ctx.update({
            'default_model': 'hotel.folio',
            'default_res_id': self._ids[0],
            'default_use_template': bool(template_id),
            'default_template_id': template_id,
            'default_composition_mode': 'comment',
            'force_send': True,
            'mark_so_as_sent': True
        })
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'views': [(compose_form_id, 'form')],
            'view_id': compose_form_id,
            'target': 'new',
            'context': ctx,
            'force_send': True
        }

    @api.multi
    def send_exit_mail(self):
        '''
        This function opens a window to compose an email,
        template message loaded by default.
        @param self: object pointer
        '''
        # Debug Stop -------------------
        # import wdb; wdb.set_trace()
        # Debug Stop -------------------
        self.ensure_one()
        ir_model_data = self.env['ir.model.data']
        try:
            template_id = ir_model_data.get_object_reference(
                'hotel',
                'mail_template_hotel_exit')[1]
        except ValueError:
            template_id = False
        try:
            compose_form_id = ir_model_data.get_object_reference(
                'mail',
                'email_compose_message_wizard_form')[1]
        except ValueError:
            compose_form_id = False
        ctx = dict()
        ctx.update({
            'default_model': 'hotel.reservation',
            'default_res_id': self._ids[0],
            'default_use_template': bool(template_id),
            'default_template_id': template_id,
            'default_composition_mode': 'comment',
            'force_send': True,
            'mark_so_as_sent': True
        })
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'views': [(compose_form_id, 'form')],
            'view_id': compose_form_id,
            'target': 'new',
            'context': ctx,
            'force_send': True
        }


    @api.multi
    def send_cancel_mail(self):
        '''
        This function opens a window to compose an email,
        template message loaded by default.
        @param self: object pointer
        '''
        # Debug Stop -------------------
        #import wdb; wdb.set_trace()
        # Debug Stop -------------------
        self.ensure_one()
        ir_model_data = self.env['ir.model.data']
        try:
            template_id = ir_model_data.get_object_reference(
                'hotel',
                'mail_template_hotel_cancel')[1]
        except ValueError:
            template_id = False
        try:
            compose_form_id = ir_model_data.get_object_reference(
                'mail',
                'email_compose_message_wizard_form')[1]
        except ValueError:
            compose_form_id = False
        ctx = dict()
        ctx.update({
            'default_model': 'hotel.reservation',
            'default_res_id': self._ids[0],
            'default_use_template': bool(template_id),
            'default_template_id': template_id,
            'default_composition_mode': 'comment',
            'force_send': True,
            'mark_so_as_sent': True
        })
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'views': [(compose_form_id, 'form')],
            'view_id': compose_form_id,
            'target': 'new',
            'context': ctx,
            'force_send': True
        }

    @api.model
    def reservation_reminder_24hrs(self):
        """
        This method is for scheduler
        every 1day scheduler will call this method to
        find all tomorrow's reservations.
        ----------------------------------------------
        @param self: The object pointer
        @return: send a mail
        """
        now_date = fields.Datetime.now()
        ir_model_data = self.env['ir.model.data']
        template_id = ir_model_data.get_object_reference(
            'hotel_reservation',
            'mail_template_reservation_reminder_24hrs')[1]
        template_rec = self.env['mail.template'].browse(template_id)
        for reserv_rec in self.search([]):
            checkin_date = datetime.strptime(reserv_rec.checkin, dt)
            difference = relativedelta(now_date, checkin_date)
            if(difference.days == -1 and reserv_rec.partner_id.email and
               reserv_rec.state == 'confirm'):
                template_rec.send_mail(reserv_rec.id, force_send=True)
        return True

    @api.multi
    def get_grouped_reservations_json(self, state, import_all=False):
        self.ensure_one()
        info_grouped = []
        for rline in self.room_lines:
            if (import_all or rline.to_send) and \
                not rline.parent_reservation and rline.state == state:
                dates = rline.get_real_checkin_checkout()
                vals = {
                    'num': len(
                        self.room_lines.filtered(
                            lambda r: r.get_real_checkin_checkout()[0] == dates[0] and \
                            r.get_real_checkin_checkout()[1] == dates[1] and \
                            r.room_type_id.id == rline.room_type_id.id and \
                            (r.to_send or import_all) and not r.parent_reservation and \
                            r.state == rline.state)
                    ),
                    'room_type': {
                        'id': rline.room_type_id.id,
                        'name': rline.room_type_id.name,
                    },
                    'checkin': dates[0],
                    'checkout': dates[1],
                    'nights': len(rline.reservation_line_ids),
                    'adults': rline.adults,
                    'childrens': rline.children,
                }
                founded = False
                for srline in info_grouped:
                    if srline['num'] == vals['num'] and \
                        srline['room_type']['id'] == vals['room_type']['id'] and \
                        srline['checkin'] == vals['checkin'] and \
                        srline['checkout'] == vals['checkout']:
                        founded = True
                        break
                if not founded:
                    info_grouped.append(vals)
        return sorted(sorted(info_grouped,key=lambda k: k['num'], reverse=True),
                      key=lambda k: k['room_type']['id'])
