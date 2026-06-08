import schedule as schedule
import json
from django.contrib import admin, messages
from django.contrib.admin import register
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group, User as AUser
from django.utils.html import format_html
from django.urls import reverse, path
from django.utils.safestring import mark_safe
from django.shortcuts import render, redirect
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponseRedirect
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Count, Sum, Avg
from django.db.models.functions import TruncDate

from django import forms

from backend.models import User, Otp, Token, PasswordResetToken, Category, Slide, HomeBanner, HomeGenderTileImage, Product, ProductOption, ProductImage, \
    PageItem, Order, OrderedProduct, Notification, ContactInfo, InformMe, AppVersion, ServiceOption, \
    ServiceCategory, ServiceSubCategory, Service, ServiceImage, ServiceBooking, ServicePageItem, Vendor, VendorToken, CartItem, \
    ProductBooking, UserAddress, ServiceCategoryAvailability, PageItemAvailability, ServiceableLocation, \
    CategoryAvailability, HomePageItem, UserDevice, AdminNotificationLog, ArtistAvailability, Coupon, CouponUsage, \
    ReferralSettings, Referral, WalletTransaction, ServiceVendor, ServiceVendorToken, TrialSettings, TrialBooking, TrialItem, ScreenViewEvent, CustomerLocationPing

admin.site.unregister(Group)
admin.site.unregister(AUser)

admin.site.site_header = "rental Cloths Admin"
admin.site.site_title = "rental-Cloths  Admin"
admin.site.index_title = "Welcome to rental Cloths  Admin Panel"

@admin.register(ScreenViewEvent)
class ScreenViewEventAdmin(admin.ModelAdmin):
    change_list_template = 'admin/backend/screenviewevent/change_list.html'
    list_display = ['screen', 'user_link', 'device_id', 'session_id', 'duration_seconds', 'started_at', 'ended_at']
    list_filter = ['screen', 'platform', 'app_version']
    search_fields = ['screen', 'user__email', 'user__phone', 'device_id', 'session_id']
    readonly_fields = ['id', 'started_at', 'ended_at', 'duration_seconds']
    date_hierarchy = 'started_at'
    ordering = ['-started_at']

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'user/<int:user_id>/',
                self.admin_site.admin_view(self.user_timeline_view),
                name='backend_screen_user_timeline',
            ),
            path(
                'screen-analytics/',
                self.admin_site.admin_view(self.screen_analytics_view),
                name='backend_screen_analytics',
            ),
            path(
                'app-opens/',
                self.admin_site.admin_view(self.app_opens_view),
                name='backend_app_opens',
            ),
            path(
                'dashboard/',
                self.admin_site.admin_view(self.analytics_dashboard_view),
                name='backend_analytics_dashboard',
            ),
        ]
        return custom_urls + urls

    def user_link(self, obj):
        """
        Clickable user → shows per-user screen timeline.
        If user is NULL, show device_id (guest/unauthenticated analytics call).
        """
        if getattr(obj, 'user_id', None):
            try:
                url = reverse('admin:backend_screen_user_timeline', args=[obj.user_id])
                label = getattr(obj.user, 'fullname', '') or getattr(obj.user, 'email', '') or f'User {obj.user_id}'
                return format_html('<a href="{}">{}</a>', url, label)
            except Exception:
                return obj.user
        return obj.device_id or '-'
    user_link.short_description = 'User'
    user_link.admin_order_field = 'user'

    def user_timeline_view(self, request, user_id: int):
        """
        User-wise screen list with durations.
        """
        qs = (
            ScreenViewEvent.objects
            .select_related('user')
            .filter(user_id=user_id)
            .order_by('-started_at')
        )
        user = User.objects.filter(id=user_id).first()

        # Per-screen totals for this user
        screen_totals = (
            qs.values('screen')
            .annotate(opens=Count('id'), total_seconds=Sum('duration_seconds'), avg_seconds=Avg('duration_seconds'))
            .order_by('-opens')
        )
        overall = qs.aggregate(opens=Count('id'), total_seconds=Sum('duration_seconds'))

        context = {
            'title': 'User screen analytics',
            'user_obj': user,
            'events': qs[:500],  # keep page fast; can add pagination later
            'screen_totals': screen_totals,
            'overall_opens': int(overall.get('opens') or 0),
            'overall_total_seconds': int(overall.get('total_seconds') or 0),
        }
        return render(request, 'admin/user_screen_timeline.html', context=context)

    def screen_analytics_view(self, request):
        """
        Simple per-screen aggregates for admin analysis.
        """
        qs = ScreenViewEvent.objects.all()
        rows = (
            qs.values('screen')
            .annotate(
                opens=Count('id'),
                unique_users=Count('user', distinct=True),
                total_seconds=Sum('duration_seconds'),
                avg_seconds=Avg('duration_seconds'),
            )
            .order_by('-opens')
        )
        return render(
            request,
            'admin/screen_analytics.html',
            context={'rows': rows},
        )

    def app_opens_view(self, request):
        """
        Daily app opens summary (based on ScreenViewEvent where screen='app_open').
        """
        qs = ScreenViewEvent.objects.filter(screen='app_open')
        rows = (
            qs.extra(select={'day': "date(started_at)"})
            .values('day')
            .annotate(
                opens=Count('id'),
                unique_users=Count('user', distinct=True),
                total_seconds=Sum('duration_seconds'),
            )
            .order_by('-day')
        )
        return render(
            request,
            'admin/app_opens_analytics.html',
            context={'rows': rows},
        )

    def analytics_dashboard_view(self, request):
        """
        Pretty analytics dashboard with charts for:
          - App opens over time
          - Screen opens + time spent
          - Orders over time + status breakdown
          - Users by city (based on pincode -> ServiceableLocation)
        """
        # Date range (default last 14 days)
        try:
            days = int(request.GET.get('days') or 14)
        except (TypeError, ValueError):
            days = 14
        days = max(1, min(days, 120))

        end_dt = timezone.now()
        start_dt = end_dt - timedelta(days=days)

        # ---- Screen analytics ----
        base_qs = ScreenViewEvent.objects.filter(started_at__gte=start_dt, started_at__lte=end_dt)
        app_open_qs = base_qs.filter(screen='app_open')
        screen_qs = base_qs.exclude(screen='app_open')

        app_opens_series = (
            app_open_qs.annotate(day=TruncDate('started_at'))
            .values('day')
            .annotate(opens=Count('id'))
            .order_by('day')
        )

        top_screens = (
            screen_qs.values('screen')
            .annotate(opens=Count('id'), total_seconds=Sum('duration_seconds'), avg_seconds=Avg('duration_seconds'))
            .order_by('-opens')[:12]
        )

        # ---- Orders analytics ----
        orders_qs = Order.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
        orders_series = (
            orders_qs.annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(orders=Count('id'))
            .order_by('day')
        )
        orders_status = (
            orders_qs.values('tx_status')
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
        )

        # ---- City analytics (by pincode) ----
        # Map pincode -> city name from ServiceableLocation
        loc_map = {str(l.pincode): (l.city or l.area_name or str(l.pincode)) for l in ServiceableLocation.objects.all()}

        users_by_city_counts = {}
        # NOTE: User.pincode is an IntegerField in current schema, but some legacy DBs may contain ''.
        # Avoid pincode='' filters (they crash with "expected a number but got ''").
        for u in User.objects.exclude(pincode__isnull=True).only('pincode'):
            pc_raw = getattr(u, 'pincode', None)
            if pc_raw is None:
                continue
            try:
                pc = str(int(pc_raw))
            except (TypeError, ValueError):
                continue
            city = loc_map.get(pc, pc)
            users_by_city_counts[city] = users_by_city_counts.get(city, 0) + 1
        users_by_city = sorted(
            [{'city': k, 'count': v} for k, v in users_by_city_counts.items()],
            key=lambda x: x['count'],
            reverse=True,
        )[:12]

        # Orders by city (from order.user.pincode)
        orders_by_city_counts = {}
        for o in orders_qs.select_related('user').only('user__pincode'):
            pc_raw = getattr(o.user, 'pincode', None) if getattr(o, 'user', None) else None
            if pc_raw is None:
                continue
            try:
                pc = str(int(pc_raw))
            except (TypeError, ValueError):
                continue
            city = loc_map.get(pc, pc)
            orders_by_city_counts[city] = orders_by_city_counts.get(city, 0) + 1
        orders_by_city = sorted(
            [{'city': k, 'count': v} for k, v in orders_by_city_counts.items()],
            key=lambda x: x['count'],
            reverse=True,
        )[:12]

        context = {
            'days': days,
            'start_dt': start_dt,
            'end_dt': end_dt,
            # JSON payloads for Chart.js
            'app_opens_series_json': json.dumps([
                {'day': str(r['day']), 'opens': int(r['opens'] or 0)} for r in app_opens_series
            ]),
            'top_screens_json': json.dumps([
                {
                    'screen': r['screen'],
                    'opens': int(r['opens'] or 0),
                    'total_seconds': int(r['total_seconds'] or 0),
                    'avg_seconds': float(r['avg_seconds'] or 0),
                }
                for r in top_screens
            ]),
            'orders_series_json': json.dumps([
                {'day': str(r['day']), 'orders': int(r['orders'] or 0)} for r in orders_series
            ]),
            'orders_status_json': json.dumps([
                {'status': (r['tx_status'] or 'UNKNOWN'), 'count': int(r['count'] or 0)} for r in orders_status
            ]),
            'users_by_city_json': json.dumps(users_by_city),
            'orders_by_city_json': json.dumps(orders_by_city),
        }
        return render(request, 'admin/analytics_dashboard.html', context=context)


@admin.register(CustomerLocationPing)
class CustomerLocationPingAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'user', 'device_id', 'latitude', 'longitude', 'accuracy_m', 'map_link']
    list_filter = ['platform']
    search_fields = ['device_id', 'user__email', 'user__phone']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']

    def map_link(self, obj):
        try:
            url = f"https://maps.google.com/?q={obj.latitude},{obj.longitude}"
            return format_html('<a href="{}" target="_blank">Open</a>', url)
        except Exception:
            return "-"
    map_link.short_description = "Map"


@register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['id', 'email', 'phone', 'fullname', 'referral_code', 'referral_wallet_balance', 'referred_by', 'is_banned', 'created_at']
    fieldsets = (
        ('User info', {
            'fields': ('email', 'phone', 'fullname', 'password',)
        }),
        ('Address info', {
            'fields': ('name', 'address', 'contact_no', 'pincode', 'district', 'state',)
        }),
        ('Referral & Fraud', {
            'fields': (
                'referral_code',
                'referred_by',
                'referral_wallet_balance',
                'device_id',
                'signup_ip',
                'is_banned',
                'ban_reason',
            )
        }),
    )
    readonly_fields = [
        'password',
        'email',
        'phone',
        'fullname',
        'name',
        'address',
        'pincode',
        'district',
        'state',
        'contact_no',
        'referral_code',
        'referred_by',
        'device_id',
        'signup_ip',
    ]
    search_fields = ['id','email','phone','fullname','address','pincode']
    search_help_text =  "Search by id,email,phone,fullname,address,pincode"



@register(Otp)
class OtpAdmin(admin.ModelAdmin):
    list_display = ['phone', 'otp', 'validity', 'verified']

    def has_add_permission(self, request):
        return False

@register(Token)
class TokenAdmin(admin.ModelAdmin):
    list_display = ['token', 'fcmtoken', 'user', 'created_at']

    def has_add_permission(self, request):
        return False


@register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ['token', 'user', 'validity']

    def has_add_permission(self, request):
        return False


class CategoryAdminForm(forms.ModelForm):
    """
    Admin UX: pick which ServiceableLocation(s) this category is available in.
    Stored in CategoryAvailability (category + location + is_available).
    If no locations are selected, the category is treated as available everywhere (backward compatible).
    """
    available_locations = forms.ModelMultipleChoiceField(
        queryset=ServiceableLocation.objects.filter(is_active=True).order_by('city', 'area_name', 'pincode'),
        required=False,
        widget=forms.SelectMultiple(attrs={'size': 12}),
        help_text='Select locations where this category should be visible for vendors/products.',
    )

    class Meta:
        model = Category
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and getattr(self.instance, 'pk', None):
            loc_ids = list(
                CategoryAvailability.objects.filter(
                    category=self.instance,
                    is_available=True,
                    location__is_active=True,
                ).values_list('location_id', flat=True)
            )
            self.fields['available_locations'].initial = ServiceableLocation.objects.filter(id__in=loc_ids)

    def save(self, commit=True):
        # Admin always calls save(commit=False) first, then obj.save() in save_model.
        # Filtering CategoryAvailability by an unsaved category raises ValueError.
        return super().save(commit=commit)


@register(Category)
class CategoryAdmin(admin.ModelAdmin):
    form = CategoryAdminForm
    list_display = ['id', 'name', 'gender', 'position', 'image']
    list_filter = ['gender']
    search_fields = ['name']

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not isinstance(form, CategoryAdminForm):
            return
        selected_locations = list(form.cleaned_data.get('available_locations') or [])
        CategoryAvailability.objects.filter(category=obj).delete()
        CategoryAvailability.objects.bulk_create([
            CategoryAvailability(category=obj, location=loc, is_available=True)
            for loc in selected_locations
        ])




@register(Slide)
class SlideAdmin(admin.ModelAdmin):
    list_display = ['position', 'image']


@register(HomeBanner)
class HomeBannerAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'redirect_type', 'display_order', 'is_active', 'created_at']
    list_editable = ['display_order', 'is_active']
    list_filter = ['is_active', 'redirect_type']
    search_fields = ['title', 'redirect_value']
    ordering = ['display_order', 'id']
    fields = (
        'title',
        'image',
        'redirect_type',
        'redirect_value',
        'display_order',
        'is_active',
    )


@register(HomeGenderTileImage)
class HomeGenderTileImageAdmin(admin.ModelAdmin):
    list_display = ['id', 'male_tile_image', 'female_tile_image']
    list_display_links = ['id']

    def has_add_permission(self, request):
        # Allow add only if no record exists (single config row)
        return not HomeGenderTileImage.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return True


class ProductOptionInline(admin.TabularInline):
    model = ProductOption
    extra = 0

    fields = [
        'option',
        'quantity',
        'is_rent_available',  # âœ… Boolean field
        'is_buy_available',  # âœ… Boolean field
        'option_rent_1_day',
        'option_rent_2_days',
        'option_rent_3_days',
        'option_rent_7_days',
        'option_rent_14_days',
        'option_rent_30_days',
        'option_buy_price',
        'option_buy_offer_price'
    ]

    def get_formset(self, request, obj=None, **kwargs):
        """Get formset with proper boolean handling"""
        formset = super().get_formset(request, obj, **kwargs)
        return formset


@register(ReferralSettings)
class ReferralSettingsAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'referral_reward_amount',
        'minimum_order_amount',
        'max_wallet_usage_percent',
        'reward_hold_days',
        'max_referrals_per_day',
        'created_at',
    ]
    readonly_fields = ['created_at', 'updated_at']


class TrialItemInline(admin.TabularInline):
    model = TrialItem
    fields = ['dress']
    extra = 0


class TrialSettingsAdminForm(forms.ModelForm):
    """
    Better admin UX:
    - Select enabled trial areas from existing ServiceableLocation rows
    - Enter human-friendly slots like "12 PM - 2 PM"
    Values are stored into JSON fields TrialSettings.trial_enabled_areas and TrialSettings.trial_slots.
    """
    enabled_locations = forms.ModelMultipleChoiceField(
        queryset=ServiceableLocation.objects.all().order_by('pincode'),
        required=False,
        widget=forms.SelectMultiple(attrs={'size': 12}),
        help_text='Select serviceable locations where trial is enabled.',
    )
    slots_input = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'e.g.\n10 AM - 12 PM\n12 PM - 2 PM\n6 PM - 10 PM'}),
        help_text='Enter one slot per line (stored as array).',
    )

    class Meta:
        model = TrialSettings
        fields = ['trial_fee', 'max_trial_items', 'trial_discount_enabled', 'trial_enabled_areas', 'trial_slots']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Pre-fill enabled_locations from stored area strings (match by pincode/area_name)
        stored_areas = list(getattr(self.instance, 'trial_enabled_areas', []) or [])
        if stored_areas:
            qs = ServiceableLocation.objects.none()
            try:
                qs = ServiceableLocation.objects.filter(area_name__in=stored_areas)
            except Exception:
                qs = ServiceableLocation.objects.none()
            self.fields['enabled_locations'].initial = qs

        # Pre-fill slots_input from stored slots list
        stored_slots = list(getattr(self.instance, 'trial_slots', []) or [])
        if stored_slots:
            self.fields['slots_input'].initial = "\n".join([str(s) for s in stored_slots if str(s).strip()])

        # Hide raw JSON fields from admin form (we use nicer inputs)
        self.fields['trial_enabled_areas'].widget = forms.HiddenInput()
        self.fields['trial_slots'].widget = forms.HiddenInput()

    def clean(self):
        cleaned = super().clean()

        # Locations → store area_name strings
        locations = cleaned.get('enabled_locations') or []
        enabled_areas = [str(loc.area_name) for loc in locations if getattr(loc, 'area_name', None)]
        cleaned['trial_enabled_areas'] = enabled_areas

        # Slots input (one per line) → array of strings
        slots_raw = (cleaned.get('slots_input') or '').strip()
        slots = []
        if slots_raw:
            for line in slots_raw.splitlines():
                s = line.strip()
                if s:
                    slots.append(s)
        cleaned['trial_slots'] = slots

        return cleaned


@register(TrialSettings)
class TrialSettingsAdmin(admin.ModelAdmin):
    form = TrialSettingsAdminForm
    list_display = [
        'id',
        'trial_fee',
        'max_trial_items',
        'trial_discount_enabled',
        'created_at',
    ]
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        (None, {
            'fields': ('trial_fee', 'max_trial_items', 'trial_discount_enabled')
        }),
        ('Enabled Areas', {
            'fields': ('enabled_locations',),
        }),
        ('Trial Slots', {
            'fields': ('slots_input',),
        }),
        ('(Stored JSON)', {
            'fields': ('trial_enabled_areas', 'trial_slots'),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@register(TrialBooking)
class TrialBookingAdmin(admin.ModelAdmin):
    inlines = [TrialItemInline]
    list_display = [
        'id',
        'user',
        'area',
        'trial_date',
        'time_slot',
        'trial_fee',
        'payment_status',
        'status',
        'converted_order',
        'created_at',
    ]
    list_filter = ['payment_status', 'status', 'trial_date', 'created_at']
    search_fields = ['id', 'user__email', 'user__phone', 'area', 'address']
    autocomplete_fields = ['user', 'converted_order']
    readonly_fields = ['created_at', 'updated_at', 'converted_at']


@register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'referrer',
        'referred_user',
        'referral_code',
        'reward_amount',
        'status',
        'is_suspicious',
        'created_at',
    ]
    list_filter = ['status', 'is_suspicious', 'created_at']
    search_fields = ['referrer__email', 'referred_user__email', 'referral_code']
    autocomplete_fields = ['referrer', 'referred_user']
    actions = ['mark_as_suspicious', 'approve_and_credit', 'credit_missing_wallet_repair', 'reject_referrals']

    def mark_as_suspicious(self, request, queryset):
        updated = queryset.update(is_suspicious=True)
        self.message_user(request, f"Marked {updated} referrals as suspicious.", level=messages.WARNING)

    mark_as_suspicious.short_description = "Mark selected referrals as suspicious"

    def approve_and_credit(self, request, queryset):
        """
        Admin fraud dashboard action:
        - Only credits wallet for non-suspicious, non-rewarded referrals.
        """
        credited = 0
        skipped_hold = 0
        for referral in queryset.select_related('referrer', 'referred_user'):
            # Skip suspicious, already rewarded, or not yet completed
            if referral.is_suspicious or referral.status == Referral.STATUS_REWARDED:
                continue
            if referral.status != Referral.STATUS_COMPLETED:
                continue

            referrer = referral.referrer
            if not referrer or referrer.is_banned:
                continue

            # Enforce hold period: do not credit before hold_until
            if referral.hold_until and referral.hold_until > timezone.now():
                skipped_hold += 1
                continue

            amount = referral.reward_amount
            if not amount or amount <= 0:
                continue

            # Credit wallet
            referrer.referral_wallet_balance = (referrer.referral_wallet_balance or 0) + amount
            referrer.save(update_fields=['referral_wallet_balance'])

            WalletTransaction.objects.create(
                user=referrer,
                amount=amount,
                type=WalletTransaction.TYPE_CREDIT,
                description=f"Referral reward for {referral.referred_user.email}",
            )

            referral.status = Referral.STATUS_REWARDED
            referral.rewarded_at = timezone.now()
            referral.save(update_fields=['status', 'rewarded_at'])
            credited += 1
            # Push notification to referrer: wallet credited
            try:
                from backend.fcm_utils import send_fcm_to_user
                amount_int = int(amount)
                send_fcm_to_user(
                    referrer,
                    'Wallet credited 💰',
                    f'₹{amount_int} has been credited to your referral wallet.',
                    data={'screen': 'referral', 'type': 'referral_wallet_credited'},
                )
            except Exception as e:
                self.message_user(request, f'Push notification failed: {e}', level=messages.WARNING)

        if credited:
            self.message_user(request, f"Credited wallet for {credited} referrals.", level=messages.SUCCESS)
        if skipped_hold:
            self.message_user(
                request,
                f"Skipped {skipped_hold} referrals still in hold period.",
                level=messages.WARNING,
            )
        if not credited and not skipped_hold:
            self.message_user(request, "No referrals were eligible for credit.", level=messages.INFO)

    approve_and_credit.short_description = "Approve & credit wallet for selected referrals"

    def credit_missing_wallet_repair(self, request, queryset):
        """
        Repair: For referrals already marked REWARDED but where the referrer's wallet
        was never credited (e.g. status was set manually). Credits wallet and creates
        WalletTransaction so balance and history match the status.
        """
        repaired = 0
        for referral in queryset.select_related('referrer', 'referred_user'):
            if referral.status != Referral.STATUS_REWARDED:
                continue
            referrer = referral.referrer
            if not referrer or referrer.is_banned:
                continue
            amount = referral.reward_amount
            if not amount or amount <= 0:
                continue
            # Check if we already have a matching credit (same user, amount, description pattern)
            desc_substr = referral.referred_user.email or str(referral.referred_user_id)
            already = WalletTransaction.objects.filter(
                user=referrer,
                type=WalletTransaction.TYPE_CREDIT,
                amount=amount,
                description__icontains=desc_substr,
            ).exists()
            if already:
                continue

            referrer.referral_wallet_balance = (referrer.referral_wallet_balance or 0) + amount
            referrer.save(update_fields=['referral_wallet_balance'])
            WalletTransaction.objects.create(
                user=referrer,
                amount=amount,
                type=WalletTransaction.TYPE_CREDIT,
                description=f"Referral reward for {referral.referred_user.email}",
            )
            repaired += 1
            # Push notification to referrer: wallet credited (repair)
            try:
                from backend.fcm_utils import send_fcm_to_user
                amount_int = int(amount)
                send_fcm_to_user(
                    referrer,
                    'Wallet credited 💰',
                    f'₹{amount_int} has been credited to your referral wallet.',
                    data={'screen': 'referral', 'type': 'referral_wallet_credited'},
                )
            except Exception as e:
                self.message_user(request, f'Push notification failed: {e}', level=messages.WARNING)

        if repaired:
            self.message_user(
                request,
                f"Repaired: credited wallet for {repaired} referral(s) that were marked Rewarded but had no wallet credit.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "No referrals needed repair (either not Rewarded or already have a matching wallet credit).",
                level=messages.INFO,
            )

    credit_missing_wallet_repair.short_description = "Credit missing wallet (repair rewarded referrals)"

    def reject_referrals(self, request, queryset):
        updated = queryset.exclude(status=Referral.STATUS_REWARDED).update(
            status=Referral.STATUS_REJECTED
        )
        self.message_user(request, f"Rejected {updated} referrals.", level=messages.WARNING)

    reject_referrals.short_description = "Reject selected referrals"


@register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'type', 'amount', 'description', 'created_at']
    list_filter = ['type', 'created_at']
    search_fields = ['user__email', 'description']
    autocomplete_fields = ['user', 'order', 'service_booking']


# admin.py - Updated ProductAdmin (replace the existing ProductAdmin class)

@register(Product)
class ProductAdmin(admin.ModelAdmin):
    inlines = [ProductOptionInline]
    list_display = ['id', 'vendor', 'category', 'title', 'position',
                    'options_pricing_preview', 'delivery_charge', 'security_amount', 'cod', 'created_at', 'updated_at']
    readonly_fields = ['star_1', 'star_2', 'star_3', 'star_4', 'star_5', 'options_pricing_overview']
    list_filter = ['cod', 'category', 'vendor', 'requires_date_selection']
    search_fields = ['id', 'title', 'vendor__name', 'vendor__vendor_id']
    search_help_text = "Search by Id, title, vendor name, vendor ID"

    fieldsets = (
        ('Basic Information', {
            'fields': ('vendor', 'category', 'title', 'description', 'position')
        }),
        ('Ã°Å¸â€™Â° Fallback Rental Pricing - Rent Options', {
            'fields': (
                'rent_price_1_day',
                'rent_price_2_days',
                'rent_price_3_days',
                'rent_price_7_days',
                'rent_price_14_days',
                'rent_price_30_days',
            ),
            'classes': ('collapse',),
            'description': 'Ã¢Å¡ Ã¯Â¸Â These are FALLBACK prices used only when ProductOptions have no custom pricing. Set ProductOption pricing for actual rental rates.'
        }),
        ('Ã°Å¸â€™Â³ Fallback Purchase Pricing', {
            'fields': ('buy_price', 'buy_offer_price'),
            'classes': ('collapse',),
            'description': 'Ã¢Å¡ Ã¯Â¸Â Fallback purchase prices. Set ProductOption pricing for actual purchase rates.'
        }),
        ('Ã°Å¸Å¡Å¡ Delivery Settings', {
            'fields': ('delivery_charge', 'security_amount', 'cod'),
            'description': 'Ã¢Å“â€¦ Delivery charge and Cash on Delivery availability'
        }),
        ('Ã°Å¸Å½Â¯ Product Options Pricing Overview', {
            'fields': ('options_pricing_overview',),
            'description': 'Ã¢Å“Â¨ View all ProductOption rental pricing (PRIMARY pricing source)'
        }),
        ('Ã°Å¸â€œâ€¦ Date Booking Settings', {
            'fields': ('requires_date_selection', 'max_bookings_per_date'),
            'classes': ('collapse',),
        }),
        ('Ã¢Â­Â Ratings', {
            'fields': ('star_5', 'star_4', 'star_3', 'star_2', 'star_1'),
            'classes': ('collapse',),
        }),
    )

    def options_pricing_preview(self, obj):
        """Ã¢Å“Â¨ Show rental pricing from ProductOptions (PRIMARY source)"""
        options = obj.options_set.all()[:3]  # Show first 3 options

        if not options:
            return format_html(
                '<div style="font-size: 11px; color: #999;">'
                'Ã¢Å¡ Ã¯Â¸Â No options yet'
                '</div>'
            )

        html = '<div style="font-size: 11px;">'

        for idx, option in enumerate(options):
            # Get option pricing
            rent_1d = option.get_rental_price('1_day')
            rent_7d = option.get_rental_price('7_days')
            buy = option.get_buy_offer_price() or option.get_buy_price()

            # Check if option has custom pricing
            has_custom = (
                    option.option_rent_1_day > 0 or
                    option.option_rent_7_days > 0 or
                    option.option_buy_price > 0
            )

            badge_color = '#10B981' if has_custom else '#6B7280'
            badge_text = 'Ã¢Å“Â¨ Custom' if has_custom else 'Ã°Å¸â€œâ€¹ Default'

            html += f'''
            <div style="margin-bottom: 6px; padding: 6px; background: {'#ECFDF5' if has_custom else '#F3F4F6'}; 
                 border-radius: 6px; border-left: 3px solid {badge_color};">
                <div style="font-weight: 600; color: #1F2937; margin-bottom: 3px;">
                    {option.option or 'Standard'} 
                    <span style="background: {badge_color}; color: white; padding: 2px 6px; 
                           border-radius: 8px; font-size: 9px; margin-left: 4px;">{badge_text}</span>
                </div>
                <div style="color: #3B82F6;">Ã°Å¸â€œâ€¦ 1D: Ã¢â€šÂ¹{rent_1d}</div>
                <div style="color: #8B5CF6;">Ã°Å¸â€œâ€¦ 7D: Ã¢â€šÂ¹{rent_7d}</div>
                <div style="color: #10B981;">Ã°Å¸â€™Â° Buy: Ã¢â€šÂ¹{buy}</div>
            </div>
            '''

        if obj.options_set.count() > 3:
            remaining = obj.options_set.count() - 3
            html += f'<div style="color: #6B7280; font-style: italic;">...and {remaining} more options</div>'

        html += '</div>'
        return format_html(html)

    options_pricing_preview.short_description = 'Ã°Å¸â€™Å½ Options Pricing'

    def options_pricing_overview(self, obj):
        """Ã¢Å“Â¨ Complete overview of ALL ProductOptions pricing"""
        options = obj.options_set.all()

        if not options:
            return format_html(
                '<div style="padding: 20px; background: #FEF3C7; border-radius: 12px; text-align: center;">'
                '<h3 style="color: #92400E; margin: 0;">Ã¢Å¡ Ã¯Â¸Â No Product Options</h3>'
                '<p style="color: #78350F; margin: 10px 0 0 0;">Add ProductOptions to set rental pricing</p>'
                '</div>'
            )

        html = '''
        <div style="padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
             border-radius: 12px; color: white; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">

        <h2 style="margin: 0 0 20px 0; color: white; font-size: 22px;">
            Ã°Å¸â€™Å½ ProductOptions Pricing Overview
        </h2>
        '''

        # Summary statistics
        total_options = options.count()
        custom_pricing_count = sum(1 for opt in options if (
                opt.option_rent_1_day > 0 or opt.option_rent_7_days > 0 or
                opt.option_buy_price > 0 or opt.option_buy_offer_price > 0
        ))
        default_pricing_count = total_options - custom_pricing_count

        html += f'''
        <div style="background: rgba(255,255,255,0.15); padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; text-align: center;">
                <div>
                    <div style="font-size: 28px; font-weight: bold; color: #FFD700;">
                        {total_options}
                    </div>
                    <div style="font-size: 12px; opacity: 0.9;">Total Options</div>
                </div>
                <div>
                    <div style="font-size: 28px; font-weight: bold; color: #4ADE80;">
                        {custom_pricing_count}
                    </div>
                    <div style="font-size: 12px; opacity: 0.9;">Ã¢Å“Â¨ Custom Pricing</div>
                </div>
                <div>
                    <div style="font-size: 28px; font-weight: bold; color: #A0AEC0;">
                        {default_pricing_count}
                    </div>
                    <div style="font-size: 12px; opacity: 0.9;">Ã°Å¸â€œâ€¹ Default Pricing</div>
                </div>
            </div>
        </div>
        '''

        # Individual option details
        for option in options:
            has_custom = (
                    option.option_rent_1_day > 0 or option.option_rent_7_days > 0 or
                    option.option_buy_price > 0 or option.option_buy_offer_price > 0
            )

            bg_color = 'rgba(16, 185, 129, 0.2)' if has_custom else 'rgba(255,255,255,0.1)'
            border_color = '#10B981' if has_custom else 'rgba(255,255,255,0.3)'

            html += f'''
            <div style="background: {bg_color}; padding: 15px; border-radius: 8px; 
                 margin-bottom: 12px; border: 2px solid {border_color};">

                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                    <h3 style="margin: 0; color: white; font-size: 16px;">
                        {option.option or 'Standard Option'}
                    </h3>
                    <span style="background: {'#10B981' if has_custom else '#6B7280'}; 
                           padding: 4px 12px; border-radius: 12px; font-size: 11px; font-weight: bold;">
                        {'Ã¢Å“Â¨ CUSTOM PRICING' if has_custom else 'Ã°Å¸â€œâ€¹ DEFAULT PRICING'}
                    </span>
                </div>

                <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px;">
                    <!-- Rental Pricing -->
                    <div style="background: rgba(59, 130, 246, 0.2); padding: 10px; border-radius: 6px;">
                        <div style="font-weight: bold; margin-bottom: 6px; font-size: 13px;">
                            Ã°Å¸â€œâ€¦ Rental Rates
                        </div>
                        <div style="font-size: 11px; line-height: 1.6;">
                            <div>1 Day: <strong>Ã¢â€šÂ¹{option.get_rental_price('1_day')}</strong> 
                                {'<span style="color: #FFD700;">Ã¢Â­Â</span>' if option.option_rent_1_day > 0 else ''}</div>
                            <div>7 Days: <strong>Ã¢â€šÂ¹{option.get_rental_price('7_days')}</strong>
                                {'<span style="color: #FFD700;">Ã¢Â­Â</span>' if option.option_rent_7_days > 0 else ''}</div>
                            <div>30 Days: <strong>Ã¢â€šÂ¹{option.get_rental_price('30_days')}</strong>
                                {'<span style="color: #FFD700;">Ã¢Â­Â</span>' if option.option_rent_30_days > 0 else ''}</div>
                        </div>
                    </div>

                    <!-- Purchase Pricing -->
                    <div style="background: rgba(16, 185, 129, 0.2); padding: 10px; border-radius: 6px;">
                        <div style="font-weight: bold; margin-bottom: 6px; font-size: 13px;">
                            Ã°Å¸â€™Â° Purchase Price
                        </div>
                        <div style="font-size: 11px; line-height: 1.6;">
                            <div>Price: <strong>Ã¢â€šÂ¹{option.get_buy_price()}</strong>
                                {'<span style="color: #FFD700;">Ã¢Â­Â</span>' if option.option_buy_price > 0 else ''}</div>
                            <div>Offer: <strong>Ã¢â€šÂ¹{option.get_buy_offer_price() or 0}</strong>
                                {'<span style="color: #FFD700;">Ã¢Â­Â</span>' if option.option_buy_offer_price > 0 else ''}</div>
                            <div style="margin-top: 4px; padding-top: 4px; border-top: 1px solid rgba(255,255,255,0.3);">
                                Stock: <strong>{option.quantity}</strong> units
                            </div>
                        </div>
                    </div>
                </div>

                <div style="margin-top: 8px; padding: 6px; background: rgba(0,0,0,0.2); 
                     border-radius: 4px; font-size: 10px; text-align: center;">
                    <a href="/admin/backend/productoption/{option.id}/change/" 
                       style="color: #FFD700; text-decoration: none; font-weight: bold;">
                        Ã¢Å“ÂÃ¯Â¸Â Edit This Option's Pricing Ã¢â€ â€™
                    </a>
                </div>
            </div>
            '''

        # Legend
        html += '''
        <div style="margin-top: 15px; padding: 12px; background: rgba(255,255,255,0.1); 
             border-radius: 8px; font-size: 11px;">
            <strong>Ã°Å¸â€œÅ’ Legend:</strong><br>
            <span style="color: #FFD700;">Ã¢Â­Â</span> = Custom pricing set for this option<br>
            <span style="color: #A0AEC0;">Ã°Å¸â€œâ€¹</span> = Using product fallback pricing<br>
            <span style="color: #10B981;">Ã¢Å“Â¨</span> = Option has at least one custom price
        </div>

        <div style="margin-top: 15px; padding: 12px; background: rgba(255, 215, 0, 0.2); 
             border-radius: 8px; border-left: 4px solid #FFD700;">
            <strong style="color: #FFD700;">Ã°Å¸â€™Â¡ Pro Tip:</strong> 
            <span style="font-size: 12px;">
                Set custom pricing on ProductOptions for accurate rental rates. 
                Product-level pricing is only used as fallback when option pricing is 0.
            </span>
        </div>

        </div>
        '''

        return format_html(html)

    options_pricing_overview.short_description = 'Ã°Å¸â€™Å½ Complete Options Pricing'

    def save_related(self, request, form, formsets, change):
        """Override to ensure boolean fields in inlines are saved correctly"""
        super().save_related(request, form, formsets, change)

        # Force save all product options with explicit boolean conversion
        if hasattr(form.instance, 'options_set'):
            for option in form.instance.options_set.all():
                # Re-save to ensure booleans are stored correctly
                option.save(update_fields=[
                    'is_rent_available',
                    'is_buy_available'
                ])
                print(f"ðŸ’¾ Verified {option.option}: Rent={option.is_rent_available}, Buy={option.is_buy_available}")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('vendor', 'category').prefetch_related('options_set')


# admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from backend.models import ProductOption, ProductImage


class ProductImageInline(admin.TabularInline):
    list = ['image', 'position']
    model = ProductImage
    extra = 0
    min_num = 1


@admin.register(ProductOption)
class ProductOptionAdmin(admin.ModelAdmin):
    inlines = [ProductImageInline]

    list_display = [
        'id',
        'product_link',
        'option',
        'quantity',
        'is_rent_available',  # âœ… Editable checkbox
        'is_buy_available',  # âœ… Editable checkbox
        'availability_badges',  # Shows status with icons
        'pricing_status_badge',  # Shows what pricing is set
        'quick_pricing_preview'  # Shows actual prices
    ]

    # âœ… Make availability fields editable directly in list
    list_editable = ['is_rent_available', 'is_buy_available']

    search_fields = ['product__title', 'option', 'quantity']
    search_help_text = 'Search by Product, Option, Quantity'
    readonly_fields = ['complete_pricing_display', 'pricing_comparison']
    list_filter = ['product__category', 'product__vendor', 'is_rent_available', 'is_buy_available']

    fieldsets = (
        ('ðŸ“¦ Basic Information', {
            'fields': ('product', 'option', 'quantity'),
            'description': 'ðŸ“‹ Product variant details'
        }),
        ('ðŸŽ¯ Availability Settings', {
            'fields': ('is_rent_available', 'is_buy_available'),
            'description': 'âœ… Enable/disable rent and buy options for this variant'
        }),
        ('ðŸ’° Standard Pricing (Override Product)', {
            'fields': ('option_price', 'option_offer_price'),
            'description': 'ðŸ’¡ Leave as 0 to use product-level pricing. Set custom values to override.'
        }),
        ('ðŸ“… Rental Pricing (Per Day/Duration)', {
            'fields': (
                ('option_rent_1_day', 'option_rent_2_days'),
                ('option_rent_3_days', 'option_rent_7_days'),
                ('option_rent_14_days', 'option_rent_30_days'),
            ),
            'description': 'ðŸ’¡ Custom rental rates for this specific option. Set to 0 to use product defaults.'
        }),
        ('ðŸ›’ Purchase Pricing (Buy Outright)', {
            'fields': ('option_buy_price', 'option_buy_offer_price'),
            'description': 'ðŸ’¡ Special purchase prices for this option. Set to 0 to use product pricing.'
        }),
        ('ðŸ“Š Pricing Preview & Comparison', {
            'fields': ('complete_pricing_display', 'pricing_comparison'),
            'classes': ('wide',),
        }),
    )

    def product_link(self, obj):
        """Link to product admin page"""
        url = reverse('admin:backend_product_change', args=[obj.product.id])
        return format_html('<a href="{}">{}</a>', url, obj.product.title[:40])

    product_link.short_description = 'Product'

    # âœ… UPGRADED: Conditional availability badges
    def availability_badges(self, obj):
        """Show availability status with colored badges - CONDITIONAL"""
        html = '<div style="display: flex; gap: 5px; flex-wrap: wrap;">'

        # âœ… Only show Rent badge if rent is available
        if obj.is_rent_available:
            html += '''
            <span style="background: #10B981; color: white; padding: 4px 8px; 
                        border-radius: 10px; font-size: 10px; font-weight: bold;">
                âœ… Rent
            </span>
            '''
        else:
            html += '''
            <span style="background: #EF4444; color: white; padding: 4px 8px; 
                        border-radius: 10px; font-size: 10px; font-weight: bold; opacity: 0.5;">
                âŒ Rent
            </span>
            '''

        # âœ… Only show Buy badge if buy is available
        if obj.is_buy_available:
            html += '''
            <span style="background: #10B981; color: white; padding: 4px 8px; 
                        border-radius: 10px; font-size: 10px; font-weight: bold;">
                âœ… Buy
            </span>
            '''
        else:
            html += '''
            <span style="background: #EF4444; color: white; padding: 4px 8px; 
                        border-radius: 10px; font-size: 10px; font-weight: bold; opacity: 0.5;">
                âŒ Buy
            </span>
            '''

        html += '</div>'
        return format_html(html)

    availability_badges.short_description = 'ðŸŽ¯ Status'

    # âœ… UPGRADED: Conditional pricing badges
    def pricing_status_badge(self, obj):
        """Show pricing badges - CONDITIONAL based on availability"""
        badges = []

        # Standard pricing (always show if set)
        has_standard = obj.option_price > 0 or obj.option_offer_price > 0
        if has_standard:
            badges.append(
                '<span style="background: #3B82F6; color: white; padding: 2px 6px; '
                'border-radius: 8px; font-size: 10px; margin-right: 3px;">ðŸ“‹ STD</span>'
            )

        # âœ… RENTAL badge - only if rent is available
        has_rental = any([
            obj.option_rent_1_day > 0,
            obj.option_rent_2_days > 0,
            obj.option_rent_3_days > 0,
            obj.option_rent_7_days > 0,
            obj.option_rent_14_days > 0,
            obj.option_rent_30_days > 0,
        ])

        if obj.is_rent_available and has_rental:
            badges.append(
                '<span style="background: #8B5CF6; color: white; padding: 2px 6px; '
                'border-radius: 8px; font-size: 10px; margin-right: 3px;">ðŸ“… RENT</span>'
            )
        elif obj.is_rent_available and not has_rental:
            badges.append(
                '<span style="background: #F59E0B; color: white; padding: 2px 6px; '
                'border-radius: 8px; font-size: 10px; margin-right: 3px;">âš ï¸ RENT (Default)</span>'
            )

        # âœ… BUY badge - only if buy is available
        has_buy = obj.option_buy_price > 0 or obj.option_buy_offer_price > 0

        if obj.is_buy_available and has_buy:
            badges.append(
                '<span style="background: #10B981; color: white; padding: 2px 6px; '
                'border-radius: 8px; font-size: 10px;">ðŸ›’ BUY</span>'
            )
        elif obj.is_buy_available and not has_buy:
            badges.append(
                '<span style="background: #F59E0B; color: white; padding: 2px 6px; '
                'border-radius: 8px; font-size: 10px;">âš ï¸ BUY (Default)</span>'
            )

        if badges:
            return format_html(''.join(badges))

        return format_html(
            '<span style="color: #9CA3AF; font-size: 10px;">ðŸ“‹ Using Product Defaults</span>'
        )

    pricing_status_badge.short_description = 'Pricing Type'

    # âœ… UPGRADED: Conditional price preview
    def quick_pricing_preview(self, obj):
        """Quick preview of prices - CONDITIONAL based on availability"""
        html = '<div style="font-size: 10px; line-height: 1.4;">'

        # Standard price (always show)
        standard = obj.get_offer_price() or obj.get_price()
        html += f'<div style="color: #3B82F6;">ðŸ’° Rs {standard}</div>'

        # âœ… RENTAL price - only if rent available
        if obj.is_rent_available:
            rent_1d = obj.get_rental_price('1_day')
            html += f'<div style="color: #8B5CF6;">ðŸ“… Rs {rent_1d}/day</div>'
        else:
            html += '<div style="color: #9CA3AF; opacity: 0.5;">ðŸ“… Rent N/A</div>'

        # âœ… BUY price - only if buy available
        if obj.is_buy_available:
            buy = obj.get_buy_offer_price() or obj.get_buy_price()
            html += f'<div style="color: #10B981;">ðŸ›’ Rs {buy}</div>'
        else:
            html += '<div style="color: #9CA3AF; opacity: 0.5;">ðŸ›’ Buy N/A</div>'

        html += '</div>'
        return format_html(html)

    quick_pricing_preview.short_description = 'Quick Prices'

    # âœ… CRITICAL: Override save_model to ensure booleans are saved correctly
    def save_model(self, request, obj, form, change):
        """
        âœ… CRITICAL: Properly handle boolean fields from admin form
        """
        print(f"\n{'=' * 60}")
        print(f"ðŸ”§ ADMIN SAVE - {obj.option}")
        print(f"{'=' * 60}")

        # Get values from form's cleaned_data
        rent_available = form.cleaned_data.get('is_rent_available')
        buy_available = form.cleaned_data.get('is_buy_available')

        print(f"ðŸ“¥ Form Data (cleaned_data):")
        print(f"   is_rent_available = {rent_available} (type: {type(rent_available).__name__})")
        print(f"   is_buy_available = {buy_available} (type: {type(buy_available).__name__})")

        # âœ… Force proper boolean conversion
        obj.is_rent_available = bool(rent_available) if rent_available is not None else True
        obj.is_buy_available = bool(buy_available) if buy_available is not None else True

        print(f"ðŸ“¤ Setting on Model:")
        print(f"   obj.is_rent_available = {obj.is_rent_available}")
        print(f"   obj.is_buy_available = {obj.is_buy_available}")

        # Save
        super().save_model(request, obj, form, change)

        # âœ… Verify what was actually saved to DB
        obj.refresh_from_db()
        print(f"âœ… Verified in Database:")
        print(f"   is_rent_available = {obj.is_rent_available}")
        print(f"   is_buy_available = {obj.is_buy_available}")
        print(f"{'=' * 60}\n")

    def save_formset(self, request, form, formset, change):
        """
        âœ… Handle inline edits (when editing from list view)
        """
        instances = formset.save(commit=False)

        for instance in instances:
            if isinstance(instance, ProductOption):
                print(f"\nðŸ”§ FORMSET SAVE - {instance.option}")
                print(f"   Before: rent={instance.is_rent_available}, buy={instance.is_buy_available}")

                # Force boolean
                instance.is_rent_available = bool(instance.is_rent_available)
                instance.is_buy_available = bool(instance.is_buy_available)

                print(f"   After: rent={instance.is_rent_available}, buy={instance.is_buy_available}")

            instance.save()

        formset.save_m2m()

    # âœ… UPGRADED: Complete pricing display with conditional sections
    def complete_pricing_display(self, obj):
        """Complete pricing breakdown with conditional display"""
        html = '''
        <div style="padding: 25px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
             border-radius: 12px; color: white; font-family: -apple-system, sans-serif;">
        <h2 style="margin: 0 0 20px 0; color: white; font-size: 22px;">ðŸ’° Complete Pricing Structure</h2>
        '''

        # Standard Pricing (always shown)
        html += '''
        <div style="background: rgba(255,255,255,0.1); padding: 15px; border-radius: 8px; margin-bottom: 12px;">
            <h3 style="margin: 0 0 10px 0; color: #FFD700; font-size: 16px;">ðŸ“‹ Standard Pricing</h3>
            <table style="width: 100%; color: white; font-size: 13px;">
        '''

        html += f'''
            <tr><td style="padding: 5px 0;"><strong>Price:</strong></td>
            <td style="text-align: right;"><strong>Rs {obj.get_price()}</strong> {self._get_pricing_badge(obj.option_price > 0)}</td></tr>
            <tr><td style="padding: 5px 0;"><strong>Offer Price:</strong></td>
            <td style="text-align: right;"><strong>Rs {obj.get_offer_price()}</strong> {self._get_pricing_badge(obj.option_offer_price > 0)}</td></tr>
        '''

        html += '</table></div>'

        # âœ… RENTAL PRICING - Only if rent available
        if obj.is_rent_available:
            html += '''
            <div style="background: rgba(255,255,255,0.1); padding: 15px; border-radius: 8px; margin-bottom: 12px;">
                <h3 style="margin: 0 0 10px 0; color: #A78BFA; font-size: 16px;">ðŸ“… Rental Pricing (Available)</h3>
                <table style="width: 100%; color: white; font-size: 13px;">
            '''

            html += f'''
                <tr><td style="padding: 4px 0;">1 Day:</td><td style="text-align: right;"><strong>Rs {obj.get_rental_price('1_day')}</strong> {self._get_pricing_badge(obj.option_rent_1_day > 0)}</td></tr>
                <tr><td style="padding: 4px 0;">2 Days:</td><td style="text-align: right;"><strong>Rs {obj.get_rental_price('2_days')}</strong> {self._get_pricing_badge(obj.option_rent_2_days > 0)}</td></tr>
                <tr><td style="padding: 4px 0;">3 Days:</td><td style="text-align: right;"><strong>Rs {obj.get_rental_price('3_days')}</strong> {self._get_pricing_badge(obj.option_rent_3_days > 0)}</td></tr>
                <tr><td style="padding: 4px 0;">7 Days:</td><td style="text-align: right;"><strong>Rs {obj.get_rental_price('7_days')}</strong> {self._get_pricing_badge(obj.option_rent_7_days > 0)}</td></tr>
                <tr><td style="padding: 4px 0;">14 Days:</td><td style="text-align: right;"><strong>Rs {obj.get_rental_price('14_days')}</strong> {self._get_pricing_badge(obj.option_rent_14_days > 0)}</td></tr>
                <tr><td style="padding: 4px 0;">30 Days:</td><td style="text-align: right;"><strong>Rs {obj.get_rental_price('30_days')}</strong> {self._get_pricing_badge(obj.option_rent_30_days > 0)}</td></tr>
            '''

            html += '</table></div>'
        else:
            html += '''
            <div style="background: rgba(239, 68, 68, 0.2); padding: 15px; border-radius: 8px; margin-bottom: 12px; border: 2px solid #EF4444;">
                <h3 style="margin: 0 0 10px 0; color: #FCA5A5; font-size: 16px;">ðŸ“… Rental Pricing (Disabled)</h3>
                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 13px;">
                    âŒ Rental option is currently disabled for this variant. Enable "Is rent available" to show rental pricing.
                </p>
            </div>
            '''

        # âœ… PURCHASE PRICING - Only if buy available
        if obj.is_buy_available:
            html += '''
            <div style="background: rgba(255,255,255,0.1); padding: 15px; border-radius: 8px;">
                <h3 style="margin: 0 0 10px 0; color: #4ADE80; font-size: 16px;">ðŸ›’ Purchase Pricing (Available)</h3>
                <table style="width: 100%; color: white; font-size: 13px;">
            '''

            html += f'''
                <tr><td style="padding: 5px 0;"><strong>Buy Price:</strong></td>
                <td style="text-align: right;"><strong style="font-size: 16px;">Rs {obj.get_buy_price()}</strong> {self._get_pricing_badge(obj.option_buy_price > 0)}</td></tr>
                <tr><td style="padding: 5px 0;"><strong>Buy Offer:</strong></td>
                <td style="text-align: right;"><strong style="font-size: 16px;">Rs {obj.get_buy_offer_price() or 0}</strong> {self._get_pricing_badge(obj.option_buy_offer_price > 0)}</td></tr>
            '''

            html += '</table></div>'
        else:
            html += '''
            <div style="background: rgba(239, 68, 68, 0.2); padding: 15px; border-radius: 8px; border: 2px solid #EF4444;">
                <h3 style="margin: 0 0 10px 0; color: #FCA5A5; font-size: 16px;">ðŸ›’ Purchase Pricing (Disabled)</h3>
                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 13px;">
                    âŒ Purchase option is currently disabled for this variant. Enable "Is buy available" to show purchase pricing.
                </p>
            </div>
            '''

        # Legend
        html += '''
        <div style="margin-top: 15px; padding: 8px; background: rgba(255,255,255,0.05); 
             border-radius: 5px; font-size: 11px; text-align: center;">
            <strong>Legend:</strong> 
            <span style="color: #FFD700;">â­ Custom Override</span> | 
            <span style="color: #87CEEB;">ðŸ“‹ Product Default</span>
        </div>
        </div>
        '''

        return format_html(html)

    complete_pricing_display.short_description = 'Complete Pricing Structure'

    def pricing_comparison(self, obj):
        """Compare option pricing with product pricing"""
        product = obj.product

        comparisons = []

        # Standard pricing comparison
        if obj.option_price > 0:
            diff = obj.option_price - product.price
            comparisons.append(
                f"Standard Price: Option Rs {obj.option_price} vs Product Rs {product.price} "
                f"({'âž•' if diff > 0 else 'âž–'} Rs {abs(diff)})"
            )

        # âœ… Rental comparison (only if rent available)
        if obj.is_rent_available:
            opt_rent = obj.get_rental_price('1_day')
            prod_rent = product.get_rental_price('1_day')
            if obj.option_rent_1_day > 0:
                diff = opt_rent - prod_rent
                comparisons.append(
                    f"1 Day Rent: Option Rs {opt_rent} vs Product Rs {prod_rent} "
                    f"({'âž•' if diff > 0 else 'âž–'} Rs {abs(diff)})"
                )

        # âœ… Buy pricing comparison (only if buy available)
        if obj.is_buy_available:
            if obj.option_buy_price > 0:
                opt_buy = obj.get_buy_price()
                prod_buy = product.get_buy_price()
                diff = opt_buy - prod_buy
                comparisons.append(
                    f"Buy Price: Option Rs {opt_buy} vs Product Rs {prod_buy} "
                    f"({'âž•' if diff > 0 else 'âž–'} Rs {abs(diff)})"
                )

        if comparisons:
            html = '<div style="padding: 15px; background: #FEF3C7; border-radius: 8px; border-left: 4px solid #F59E0B;">'
            html += '<h4 style="margin: 0 0 10px 0; color: #92400E;">ðŸ“Š Pricing Comparison vs Product</h4>'
            html += '<ul style="margin: 0; padding-left: 20px; color: #78350F;">'
            for comp in comparisons:
                html += f'<li style="margin-bottom: 5px;">{comp}</li>'
            html += '</ul></div>'
            return format_html(html)

        return format_html(
            '<div style="padding: 15px; background: #DBEAFE; border-radius: 8px;">'
            '<p style="margin: 0; color: #1E3A8A;">ðŸ“‹ This option uses all product-level pricing (no overrides set)</p>'
            '</div>'
        )

    pricing_comparison.short_description = 'Pricing Comparison'

    def _get_pricing_badge(self, is_custom):
        """Helper to generate pricing badge"""
        if is_custom:
            return '<span style="color: #FFD700; font-size: 10px;">â­ Custom</span>'
        return '<span style="color: #87CEEB; font-size: 10px;">ðŸ“‹ Default</span>'

    # âœ… Admin actions
    actions = [
        'enable_rent_for_selected',
        'disable_rent_for_selected',
        'enable_buy_for_selected',
        'disable_buy_for_selected',
        'enable_both_for_selected',
        'copy_pricing_from_product',
        'clear_custom_pricing',
    ]

    def enable_rent_for_selected(self, request, queryset):
        """Enable rent for selected options"""
        updated = queryset.update(is_rent_available=True)
        self.message_user(request, f'âœ… Enabled rent for {updated} option(s)')

    enable_rent_for_selected.short_description = "âœ… Enable Rent"

    def disable_rent_for_selected(self, request, queryset):
        """Disable rent for selected options"""
        updated = queryset.update(is_rent_available=False)
        self.message_user(request, f'âŒ Disabled rent for {updated} option(s)')

    disable_rent_for_selected.short_description = "âŒ Disable Rent"

    def enable_buy_for_selected(self, request, queryset):
        """Enable buy for selected options"""
        updated = queryset.update(is_buy_available=True)
        self.message_user(request, f'âœ… Enabled buy for {updated} option(s)')

    enable_buy_for_selected.short_description = "âœ… Enable Buy"

    def disable_buy_for_selected(self, request, queryset):
        """Disable buy for selected options"""
        updated = queryset.update(is_buy_available=False)
        self.message_user(request, f'âŒ Disabled buy for {updated} option(s)')

    disable_buy_for_selected.short_description = "âŒ Disable Buy"

    def enable_both_for_selected(self, request, queryset):
        """Enable both rent and buy"""
        updated = queryset.update(is_rent_available=True, is_buy_available=True)
        self.message_user(request, f'âœ… Enabled both rent and buy for {updated} option(s)')

    enable_both_for_selected.short_description = "âœ… Enable Both"

    def copy_pricing_from_product(self, request, queryset):
        """Copy product pricing to option pricing"""
        updated = 0
        for option in queryset:
            product = option.product
            option.option_price = product.price
            option.option_offer_price = product.offer_price
            option.option_rent_1_day = product.rent_price_1_day
            option.option_rent_2_days = product.rent_price_2_days
            option.option_rent_3_days = product.rent_price_3_days
            option.option_rent_7_days = product.rent_price_7_days
            option.option_rent_14_days = product.rent_price_14_days
            option.option_rent_30_days = product.rent_price_30_days
            option.option_buy_price = product.buy_price
            option.option_buy_offer_price = product.buy_offer_price
            option.save()
            updated += 1
        self.message_user(request, f'ðŸ“‹ Copied product pricing to {updated} option(s)')

    copy_pricing_from_product.short_description = "Copy pricing from product"

    def clear_custom_pricing(self, request, queryset):
        """Clear all custom pricing"""
        queryset.update(
            option_price=0,
            option_offer_price=0,
            option_rent_1_day=0,
            option_rent_2_days=0,
            option_rent_3_days=0,
            option_rent_7_days=0,
            option_rent_14_days=0,
            option_rent_30_days=0,
            option_buy_price=0,
            option_buy_offer_price=0
        )
        self.message_user(request, f'ðŸ—‘ï¸ Cleared custom pricing for {queryset.count()} option(s)')

    clear_custom_pricing.short_description = "Clear all custom pricing"

@register(PageItem)
class PageItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'position', 'image', 'category', 'viewtype']
    filter_horizontal = ['product_options']
    list_filter = ['viewtype','category']
    search_fields = ['title']
    search_help_text = "Search by title"


@admin.register(OrderedProduct)
class OrderedProductAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'order_link',
        'product_title',
        'vendor_info_badge',
        'rental_type_badge',
        'rental_dates_display',
        'quantity',
        'tx_price',
        'status_badge',
        'created_at'
    ]

    list_filter = [
        'rental_type',
        'status',
        'rental_duration',
        'created_at',
        'product_option__product__vendor'
    ]

    search_fields = [
        'id',
        'order__id',
        'product_option__product__title',
        'product_option__product__vendor__name',
        'product_option__product__vendor__phone'
    ]

    readonly_fields = [
        'order',
        'product_option',
        'product_price',
        'tx_price',
        'delivery_price',
        'quantity',
        'rating',
        'vendor_contact_info',
        'rental_info_display',
        'rental_timeline',
        'created_at',
        'updated_at'
    ]

    fieldsets = (
        ('Order Information', {
            'fields': ('order', 'product_option', 'quantity', 'status')
        }),
        ('Pricing Details', {
            'fields': ('product_price', 'tx_price', 'delivery_price')
        }),
        ('Rental Information', {
            'fields': (
                'rental_type',
                'rental_duration',
                'rental_start_date',
                'rental_end_date',
                'rental_info_display',
                'rental_timeline'
            ),
            'classes': ('wide',),
        }),
        ('Vendor Contact', {
            'fields': ('vendor_contact_info',),
            'classes': ('wide',),
        }),
        ('Additional Info', {
            'fields': ('rating', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    ordering = ['-created_at']

    def product_title(self, obj):
        return obj.product_option.product.title

    product_title.short_description = 'Product'

    def order_link(self, obj):
        url = reverse('admin:backend_order_change', args=[obj.order.id])
        return format_html('<a href="{}">{}</a>', url, str(obj.order.id)[:8].upper())

    order_link.short_description = 'Order #'

    def vendor_info_badge(self, obj):
        vendor = obj.product_option.product.vendor
        if vendor:
            phone_icon = ''
            return format_html(
                '<div style="line-height: 1.4;">'
                '<strong>{}</strong><br>'
                '<small>{} {}</small>'
                '</div>',
                vendor.name,
                phone_icon,
                vendor.phone
            )
        return '-'

    vendor_info_badge.short_description = 'Vendor'

    def rental_type_badge(self, obj):
        colors = {
            'rent': '#3B82F6',  # Blue
            'buy': '#10B981',  # Green
        }
        icons = {
            'rent': '',
            'buy': '',
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 12px; '
            'border-radius: 15px; font-weight: bold; font-size: 11px;">'
            '{} {}</span>',
            colors.get(obj.rental_type, '#666666'),
            icons.get(obj.rental_type, ''),
            obj.rental_type.upper()
        )

    rental_type_badge.short_description = 'Type'

    def rental_dates_display(self, obj):
        if obj.rental_type == 'rent' and obj.rental_start_date:
            duration_map = {
                '1_day': '1D', '2_days': '2D', '3_days': '3D',
                '7_days': '1W', '14_days': '2W', '30_days': '1M'
            }
            duration = duration_map.get(obj.rental_duration, obj.rental_duration)
            return format_html(
                '<div style="font-size: 11px; line-height: 1.4;">'
                '<strong>{}</strong><br>'
                '<span style="color: #666;">{}  {}</span>'
                '</div>',
                duration,
                obj.rental_start_date.strftime('%b %d'),
                obj.rental_end_date.strftime('%b %d') if obj.rental_end_date else '-'
            )
        return '-'

    rental_dates_display.short_description = 'Rental Period'

    def status_badge(self, obj):
        colors = {
            'ORDERED': '#FFA500',
            'OUT_FOR_DELIVERY': '#3B82F6',
            'DELIVERED': '#10B981',
            'CANCELLED': '#EF4444',
            'RETURNED': '#8B5CF6',
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; '
            'border-radius: 5px; font-weight: bold; font-size: 11px;">{}</span>',
            colors.get(obj.status, '#666666'),
            obj.status
        )

    status_badge.short_description = 'Status'

    def vendor_contact_info(self, obj):
        """Display detailed vendor contact information"""
        vendor = obj.product_option.product.vendor
        if vendor:
            return format_html(
                '<div style="padding: 15px; background: #f8f9fa; border-radius: 8px; '
                'border-left: 4px solid #6C5CE7;">'
                '<h3 style="margin: 0 0 10px 0; color: #6C5CE7;">Vendor Details</h3>'
                '<table style="width: 100%;">'
                '<tr><td style="padding: 5px 10px 5px 0; font-weight: bold;">Name:</td><td>{}</td></tr>'
                '<tr><td style="padding: 5px 10px 5px 0; font-weight: bold;">Vendor ID:</td><td>{}</td></tr>'
                '<tr><td style="padding: 5px 10px 5px 0; font-weight: bold;">Phone:</td>'
                '<td><a href="tel:{}" style="color: #10B981; font-weight: bold; text-decoration: none;">'
                ' {}</a></td></tr>'
                '<tr><td style="padding: 5px 10px 5px 0; font-weight: bold;">Email:</td>'
                '<td><a href="mailto:{}">{}</a></td></tr>'
                '<tr><td style="padding: 5px 10px 5px 0; font-weight: bold;">Address:</td><td>{}</td></tr>'
                '</table>'
                '</div>',
                vendor.name,
                vendor.vendor_id,
                vendor.phone,
                vendor.phone,
                vendor.email,
                vendor.email,
                vendor.business_address or 'N/A'
            )
        return 'No vendor assigned'

    vendor_contact_info.short_description = 'Vendor Contact Information'

    def rental_info_display(self, obj):
        """Display formatted rental information"""
        if obj.rental_type == 'buy':
            return format_html(
                '<div style="padding: 10px; background: #E8F5E9; border-radius: 5px;">'
                '<strong style="color: #10B981;"> Purchase</strong><br>'
                '<span style="color: #666;">One-time purchase</span>'
                '</div>'
            )
        elif obj.rental_type == 'rent':
            duration_map = {
                '1_day': '1 Day',
                '2_days': '2 Days',
                '3_days': '3 Days',
                '7_days': '7 Days',
                '14_days': '14 Days',
                '30_days': '30 Days',
            }
            duration = duration_map.get(obj.rental_duration, obj.rental_duration)

            return format_html(
                '<div style="padding: 10px; background: #E3F2FD; border-radius: 5px;">'
                '<strong style="color: #3B82F6;"> Rental</strong><br>'
                '<span style="color: #666;">Duration: <strong>{}</strong></span>'
                '</div>',
                duration
            )
        return 'N/A'

    rental_info_display.short_description = 'Rental Details'

    def rental_timeline(self, obj):
        """Display rental timeline"""
        if obj.rental_type == 'rent' and obj.rental_start_date and obj.rental_end_date:
            from datetime import date
            from django.utils.safestring import mark_safe  #  Add this import

            today = date.today()

            status_html = ''
            if today < obj.rental_start_date:
                days_until = (obj.rental_start_date - today).days
                status_html = f'<div style="color: #3B82F6;"> Starts in {days_until} days</div>'
            elif obj.rental_start_date <= today <= obj.rental_end_date:
                days_remaining = (obj.rental_end_date - today).days
                status_html = f'<div style="color: #10B981;"> Active ({days_remaining} days remaining)</div>'
            else:
                days_overdue = (today - obj.rental_end_date).days
                status_html = f'<div style="color: #EF4444;">  Ended {days_overdue} days ago</div>'

            return format_html(
                '<div style="padding: 10px; background: #FFF3E0; border-radius: 5px; color: #000;">'
                '<strong style="color: #000;">Timeline</strong><br>'
                '<small style="color: #000;">Start: <strong>{}</strong></small><br>'
                '<small style="color: #000;">End: <strong>{}</strong></small><br>'
                '{}'
                '</div>',
                obj.rental_start_date.strftime('%b %d, %Y'),
                obj.rental_end_date.strftime('%b %d, %Y'),
                mark_safe(status_html)
            )
        return '-'

    rental_timeline.short_description = 'Rental Timeline'

    def has_add_permission(self, request):
        return False

    actions = ['mark_as_delivered', 'mark_as_returned']

    def mark_as_delivered(self, request, queryset):
        queryset.update(status='DELIVERED')
        self.message_user(request, f"{queryset.count()} order(s) marked as delivered.")

    mark_as_delivered.short_description = "Mark as delivered"

    def mark_as_returned(self, request, queryset):
        rental_items = queryset.filter(rental_type='rent')
        rental_items.update(status='RETURNED')
        self.message_user(request, f"{rental_items.count()} rental(s) marked as returned.")

    mark_as_returned.short_description = "Mark as returned (Rentals)"



class OrderedProductInline(admin.TabularInline):
    model = OrderedProduct
    fields = ['product_option', 'quantity', 'product_price', 'tx_price', 'delivery_price', 'status', 'rating']  # Changed from 'list' to 'fields'
    readonly_fields = ['product_option', 'product_price', 'tx_price', 'delivery_price', 'quantity', 'status', 'rating']
    show_change_link = True
    extra = 0

    def has_add_permission(self, request, obj):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ProductBookingInline(admin.TabularInline):
    model = ProductBooking
    fields = ['product', 'booking_date', 'quantity_booked', 'status']
    readonly_fields = ['product', 'booking_date', 'quantity_booked']
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj):
        return False


@register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = [
        'code', 'discount_type', 'discount_value', 'minimum_order_amount',
        'maximum_discount_amount', 'is_active', 'valid_from', 'valid_until',
        'usage_limit', 'used_count', 'first_order_only', 'created_at'
    ]
    list_filter = ['is_active', 'discount_type', 'first_order_only']
    search_fields = ['code', 'description']
    filter_horizontal = ['applicable_products', 'applicable_categories']
    autocomplete_fields = ['applicable_services']
    readonly_fields = ['used_count', 'created_at', 'updated_at']
    date_hierarchy = 'valid_until'
    fieldsets = (
        (None, {
            'fields': ('code', 'description', 'is_active')
        }),
        ('Discount', {
            'fields': ('discount_type', 'discount_value', 'minimum_order_amount', 'maximum_discount_amount')
        }),
        ('Validity', {
            'fields': ('valid_from', 'valid_until', 'usage_limit', 'used_count')
        }),
        ('Restrictions', {
            'fields': ('first_order_only', 'applicable_products', 'applicable_categories', 'applicable_services')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@register(CouponUsage)
class CouponUsageAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'coupon', 'order', 'service_booking', 'used_at']
    list_filter = ['coupon', 'used_at']
    search_fields = ['user__email', 'coupon__code', 'order__id', 'service_booking__id']
    readonly_fields = ['user', 'coupon', 'order', 'service_booking', 'used_at']
    ordering = ['-used_at']

    def has_add_permission(self, request):
        return False


@register(Order)
class OrderAdmin(admin.ModelAdmin):
    inlines = [OrderedProductInline,ProductBookingInline]
    list_display = ['id','seen', 'user', 'tx_amount', 'discount_amount', 'coupon', 'payment_mode', 'address', 'tx_id', 'tx_status', 'tx_time', 'tx_msg',
                    'from_cart', 'created_at', 'updated_at']
    list_filter = ['payment_mode', 'tx_status', 'from_cart']
    ordering = ['-created_at']
    readonly_fields = ['user', 'tx_amount', 'payment_mode', 'tx_id', 'tx_time', 'tx_msg', 'from_cart']
    search_fields = ['id','user__email','address','tx_id', ]
    search_help_text = "Search by Id, user, address, tx_id"

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        old_status = None
        if change and obj.pk:
            try:
                old_order = Order.objects.get(pk=obj.pk)
                old_status = getattr(old_order, 'vendor_status', None)
            except Order.DoesNotExist:
                pass
        super().save_model(request, obj, form, change)
        new_status = getattr(obj, 'vendor_status', None)
        if new_status == 'ACCEPTED' and old_status != 'ACCEPTED':
            try:
                from backend.views import _send_accept_push_notification
                _send_accept_push_notification(obj.user, obj.id)
            except Exception as e:
                self.message_user(request, f'Push notification failed: {e}', level=messages.WARNING)
        elif new_status == 'REJECTED' and old_status != 'REJECTED':
            try:
                from backend.views import _send_reject_push_notification
                _send_reject_push_notification(obj.user, obj.id)
            except Exception as e:
                self.message_user(request, f'Push notification (reject) failed: {e}', level=messages.WARNING)

@admin.register(UserAddress)
class UserAddressAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'name', 'type', 'is_default', 'created_at']
    list_filter = ['type', 'is_default', 'created_at']
    search_fields = ['name', 'address', 'user__email']
    readonly_fields = ['created_at']


@register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'body', 'image', 'seen', 'created_at']


# ---------- Send push notification (admin: add = send form, list = history) ----------
class SendPushNotificationForm(forms.ModelForm):
    """Form for sending push from admin; includes extra field 'users' for selected users."""
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.all().order_by('email'),
        required=False,
        widget=forms.SelectMultiple(attrs={'size': 12}),
        help_text='Only for "Selected users". Users with no FCM token are skipped.',
    )
    data_payload = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': '{"screen": "orders"}'}),
        help_text='Optional JSON for deep linking.',
    )

    class Meta:
        model = AdminNotificationLog
        fields = ['title', 'body', 'target_type']

    def clean_data_payload(self):
        import json
        value = (self.cleaned_data.get('data_payload') or '').strip()
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            raise forms.ValidationError(f'Invalid JSON: {e}')

    def clean(self):
        data = super().clean()
        if data.get('target_type') == AdminNotificationLog.TARGET_SELECTED and not data.get('users'):
            raise forms.ValidationError({'users': 'Select at least one user when targeting selected users.'})
        return data


@admin.register(AdminNotificationLog)
class AdminNotificationLogAdmin(admin.ModelAdmin):
    form = SendPushNotificationForm
    list_display = ['title_short', 'target_type', 'target_count', 'created_at']
    list_filter = ['target_type', 'created_at']
    search_fields = ['title', 'body']
    readonly_fields = ['target_count', 'created_at']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']

    def title_short(self, obj):
        return (obj.title[:50] + '...') if len(obj.title) > 50 else obj.title
    title_short.short_description = 'Title'

    def get_fields(self, request, obj=None):
        if obj is None:
            return ['title', 'body', 'target_type', 'users', 'data_payload']
        return ['title', 'body', 'target_type', 'target_count', 'data', 'created_at']

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ['title', 'body', 'target_type', 'target_count', 'data', 'created_at']

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return
        from backend.fcm_utils import send_fcm_to_all_users, send_fcm_to_users
        title = form.cleaned_data['title']
        body = form.cleaned_data['body']
        target_type = form.cleaned_data['target_type']
        users = form.cleaned_data.get('users') or []
        data = form.cleaned_data.get('data_payload')
        try:
            if target_type == AdminNotificationLog.TARGET_ALL:
                success, failure = send_fcm_to_all_users(title, body, data)
            else:
                success, failure = send_fcm_to_users(users, title, body, data)
            obj.target_count = success + failure
            obj.data = data
            super().save_model(request, obj, form, change)
            self.message_user(request, f'Push sent: {success} success, {failure} failure ({obj.target_count} devices).', level=messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f'Failed to send push: {e}', level=messages.ERROR)


@admin.register(ContactInfo)
class ContactInfoAdmin(admin.ModelAdmin):
    list_display = ['id', 'phone_number']
    fields = ['phone_number']
    search_fields = ['phone_number']
    search_help_text = "Search by phone number"

    def has_add_permission(self, request):
        # Allow only one ContactInfo entry
        try:
            if ContactInfo.objects.exists():
                return False
        except Exception:
            # Table doesn't exist yet, allow adding
            pass
        return super().has_add_permission(request)



@admin.register(InformMe)
class InformMeAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'product', 'product_option',
        'price', 'offer_price', 'created_at'
    )
    list_filter = ('created_at', 'product')
    search_fields = ('user__email', 'product__title')
    readonly_fields = ('price', 'offer_price', 'created_at')


@admin.register(AppVersion)
class AppVersionAdmin(admin.ModelAdmin):
    list_display = [
        'platform',
        'version_name',
        'version_code',
        'is_active',
        'is_force_update',
        'created_at'
    ]
    list_filter = ['platform', 'is_force_update', 'is_active', 'created_at']
    search_fields = ['version_name', 'update_message', 'release_notes']
    ordering = ['-created_at']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Version Information', {
            'fields': ('platform', 'version_name', 'version_code', 'store_url'),
            'description': 'Basic version details and store URL'
        }),
        ('Update Policy', {
            'fields': ('min_supported_version', 'min_supported_code', 'is_force_update', 'is_active'),
            'description': 'Control update behavior and requirements'
        }),
        ('User Messages', {
            'fields': ('update_message', 'release_notes'),
            'description': 'Messages shown to users in the update dialog'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )



    # Add custom actions
    actions = ['make_active', 'make_inactive', 'make_force_update', 'remove_force_update']

    def make_active(self, request, queryset):
        for obj in queryset:
            # Deactivate other versions for the same platform
            AppVersion.objects.filter(platform=obj.platform, is_active=True).update(is_active=False)
            obj.is_active = True
            obj.save()
        self.message_user(request, f"{queryset.count()} version(s) activated.")

    make_active.short_description = "Activate selected versions"

    def make_inactive(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, f"{queryset.count()} version(s) deactivated.")

    make_inactive.short_description = "Deactivate selected versions"

    def make_force_update(self, request, queryset):
        queryset.update(is_force_update=True)
        self.message_user(request, f"{queryset.count()} version(s) set to force update.")

    make_force_update.short_description = "Set as force update"

    def remove_force_update(self, request, queryset):
        queryset.update(is_force_update=False)
        self.message_user(request, f"{queryset.count()} version(s) set to optional update.")

    remove_force_update.short_description = "Remove force update"


@register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'position', 'icon', 'color', 'image']
    list_editable = ['position']
    ordering = ['position']


@register(ServiceSubCategory)
class ServiceSubCategoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'category', 'position', 'image']
    list_filter = ['category']
    list_editable = ['position']
    ordering = ['category', 'position']
    search_fields = ['name', 'category__name']


class ServiceOptionInline(admin.TabularInline):
    model = ServiceOption
    extra = 0
    show_change_link = True


# admin.py - Update ServiceAdmin

@register(Service)
class ServiceAdmin(admin.ModelAdmin):
    inlines = [ServiceOptionInline]
    list_display = [
        'id', 'title', 'category', 'subcategory', 'provider_name', 'base_price',
        'rating', 'experience_years', 'languages_display',  # Ã¢Å“â€¦ NEW
        'total_portfolio_images',  # Ã¢Å“â€¦ NEW
        'availability', 'created_at'
    ]
    list_filter = ['category', 'subcategory', 'availability', 'experience_years', 'created_at']
    search_fields = ['title', 'provider_name', 'provider_phone', 'location', 'languages']
    search_help_text = "Search by title, provider name, phone, location, or languages"
    readonly_fields = ['rating', 'total_reviews', 'portfolio_preview', 'manage_availability_link', 'created_at', 'updated_at']

    fieldsets = (
        ('Calendar & Availability', {
            'fields': ('manage_availability_link',),
            'description': 'Block or book dates for this artist only. Other artists are not affected.'
        }),
        ('Service Information', {
            'fields': ('category', 'subcategory', 'title', 'description', 'base_price', 'availability', 'location')
        }),
        ('Ã°Å¸â€˜Â¤ Provider Details', {
            'fields': (
                'provider_name',
                'provider_phone',
                'provider_email',
                'experience_years',
                'languages'  # Ã¢Å“â€¦ NEW
            )
        }),
        ('Ã¢Â­Â Ratings & Reviews', {
            'fields': ('rating', 'total_reviews'),
            'classes': ('collapse',)
        }),
        ('Ã°Å¸â€“Â¼Ã¯Â¸Â Portfolio Preview', {
            'fields': ('portfolio_preview',),
            'classes': ('wide',),
            'description': 'Ã¢Å“Â¨ All images uploaded in service options will appear in the portfolio'
        }),
        ('Ã°Å¸â€œâ€¦ Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    # Ã¢Å“â€¦ NEW: Display languages in list view
    def languages_display(self, obj):
        """Display languages as badges"""
        langs = obj.get_languages_list()
        badges = []
        colors = ['#3B82F6', '#10B981', '#F59E0B', '#8B5CF6', '#EF4444']

        for idx, lang in enumerate(langs[:3]):  # Show max 3 languages
            color = colors[idx % len(colors)]
            badges.append(
                f'<span style="background: {color}; color: white; padding: 3px 8px; '
                f'border-radius: 10px; font-size: 10px; margin-right: 3px;">{lang}</span>'
            )

        if len(langs) > 3:
            badges.append(f'<span style="color: #666; font-size: 10px;">+{len(langs) - 3} more</span>')

        return format_html(''.join(badges))

    languages_display.short_description = 'Ã°Å¸â€”Â£Ã¯Â¸Â Languages'

    # Ã¢Å“â€¦ NEW: Show total portfolio images
    def total_portfolio_images(self, obj):
        """Count total images across all service options"""
        from django.db.models import Count

        total = 0
        for option in obj.options_set.all():
            total += option.images_set.count()

        if total == 0:
            return format_html(
                '<span style="color: #EF4444; font-weight: bold;">Ã¢Å¡ Ã¯Â¸Â No images</span>'
            )

        return format_html(
            '<span style="background: #10B981; color: white; padding: 4px 10px; '
            'border-radius: 10px; font-weight: bold; font-size: 11px;">Ã°Å¸â€“Â¼Ã¯Â¸Â {} images</span>',
            total
        )

    total_portfolio_images.short_description = 'Portfolio'

    def manage_availability_link(self, obj):
        if not obj or not obj.pk:
            return '-'
        url = reverse('admin:backend_artistavailability_changelist') + '?artist__id__exact=' + str(obj.pk)
        return format_html('<a href="{}" class="button" style="padding: 8px 15px; background: #417690; color: white; '
                           'text-decoration: none; border-radius: 6px;">Manage Availability</a>', url)
    manage_availability_link.short_description = 'Artist calendar (block / book dates)'

    # Ã¢Å“â€¦ NEW: Portfolio preview in admin
    def portfolio_preview(self, obj):
        """Show all portfolio images from all service options"""
        html = '<div style="padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); ' \
               'border-radius: 12px;">'

        html += '<h2 style="color: white; margin: 0 0 15px 0;">Ã°Å¸â€“Â¼Ã¯Â¸Â Complete Portfolio</h2>'

        # Get all options
        options = obj.options_set.all()

        if not options.exists():
            html += '<div style="background: rgba(255,255,255,0.1); padding: 15px; border-radius: 8px; color: white;">' \
                    'Ã¢Å¡ Ã¯Â¸Â No service options created yet. Add options to upload portfolio images.</div>'
            html += '</div>'
            return format_html(html)

        total_images = 0

        for option in options:
            images = option.images_set.all().order_by('position')
            image_count = images.count()
            total_images += image_count

            html += f'<div style="background: rgba(255,255,255,0.1); padding: 15px; border-radius: 8px; ' \
                    f'margin-bottom: 12px; color: white;">'

            html += f'<h3 style="margin: 0 0 10px 0; color: #FFD700;">Ã°Å¸â€œÂ¦ {option.option_name}</h3>'

            if image_count == 0:
                html += '<p style="color: rgba(255,255,255,0.7); margin: 0;">No images uploaded yet</p>'
            else:
                html += f'<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); ' \
                        f'gap: 10px; margin-top: 10px;">'

                for image in images:
                    if image.image:
                        html += f'<div style="position: relative; border-radius: 8px; overflow: hidden; ' \
                                f'aspect-ratio: 1; border: 2px solid rgba(255,255,255,0.3);">' \
                                f'<img src="{image.image.url}" style="width: 100%; height: 100%; object-fit: cover;" />' \
                                f'<div style="position: absolute; top: 5px; right: 5px; background: rgba(0,0,0,0.7); ' \
                                f'color: white; padding: 2px 6px; border-radius: 4px; font-size: 10px;">' \
                                f'#{image.position}</div>' \
                                f'</div>'

                html += '</div>'

            # Add link to edit this option
            html += f'<div style="margin-top: 10px; text-align: right;">' \
                    f'<a href="/admin/backend/serviceoption/{option.id}/change/" ' \
                    f'style="color: #FFD700; text-decoration: none; font-weight: bold; font-size: 11px;">' \
                    f'Ã¢Å“ÂÃ¯Â¸Â Edit This Option & Upload Images Ã¢â€ â€™</a></div>'

            html += '</div>'

        # Summary
        html += f'<div style="background: rgba(255,255,255,0.15); padding: 12px; border-radius: 8px; ' \
                f'margin-top: 15px; text-align: center; color: white;">' \
                f'<strong style="font-size: 16px;">Ã°Å¸â€œÅ  Total: {total_images} portfolio images</strong>' \
                f'</div>'

        html += '</div>'

        return format_html(html)

    portfolio_preview.short_description = 'Ã°Å¸Å½Â¨ Portfolio Gallery'


class ServiceImageInline(admin.TabularInline):
    model = ServiceImage
    fields = ['image', 'position', 'image_preview']
    readonly_fields = ['image_preview']
    extra = 1  # Show 1 empty form by default
    min_num = 1  # Require at least 1 image
    verbose_name = "Portfolio Image"
    verbose_name_plural = "Ã°Å¸â€œÂ¸ Upload Portfolio Images (These appear in the service portfolio gallery)"

    def image_preview(self, obj):
        """Show small preview of uploaded image"""
        if obj.image:
            return format_html(
                '<img src="{}" style="max-width: 100px; max-height: 100px; '
                'border-radius: 8px; object-fit: cover;" />',
                obj.image.url
            )
        return "No image uploaded"

    image_preview.short_description = 'Preview'


@register(ServiceOption)
class ServiceOptionAdmin(admin.ModelAdmin):
    inlines = [ServiceImageInline]
    list_display = [
        'id', 'service', 'option_name', 'price', 'duration',
        'images_count_badge',  # Ã¢Å“â€¦ NEW
        'available'
    ]
    list_filter = ['available', 'service__category']
    search_fields = ['service__title', 'option_name']
    search_help_text = 'Search by service title or option name'

    readonly_fields = ['images_preview']  # Ã¢Å“â€¦ NEW

    fieldsets = (
        ('Ã°Å¸â€œâ€¹ Option Details', {
            'fields': ('service', 'option_name', 'description', 'price', 'duration', 'available')
        }),
        ('Ã°Å¸â€“Â¼Ã¯Â¸Â Portfolio Images', {
            'fields': ('images_preview',),
            'description': 'Ã¢Â¬â€¡Ã¯Â¸Â Upload multiple images below using the "Service Images" section'
        }),
    )

    # Ã¢Å“â€¦ NEW: Show image count in list
    def images_count_badge(self, obj):
        """Display image count as a badge"""
        count = obj.images_set.count()

        if count == 0:
            return format_html(
                '<span style="background: #EF4444; color: white; padding: 4px 10px; '
                'border-radius: 10px; font-size: 11px; font-weight: bold;">Ã¢Å¡ Ã¯Â¸Â No images</span>'
            )

        return format_html(
            '<span style="background: #10B981; color: white; padding: 4px 10px; '
            'border-radius: 10px; font-size: 11px; font-weight: bold;">Ã°Å¸â€“Â¼Ã¯Â¸Â {} images</span>',
            count
        )

    images_count_badge.short_description = 'Images'

    # Ã¢Å“â€¦ NEW: Preview uploaded images
    def images_preview(self, obj):
        """Show preview of all uploaded images"""
        images = obj.images_set.all().order_by('position')

        if not images.exists():
            return format_html(
                '<div style="padding: 15px; background: #FEE2E2; border-radius: 8px; '
                'border-left: 4px solid #EF4444;">'
                '<strong style="color: #DC2626;">Ã¢Å¡ Ã¯Â¸Â No Images Uploaded</strong><br>'
                '<span style="color: #991B1B;">Use the "Service Images" section below to upload portfolio images</span>'
                '</div>'
            )

        html = '<div style="padding: 15px; background: #F0F9FF; border-radius: 8px; border-left: 4px solid #3B82F6;">'
        html += f'<strong style="color: #1E40AF;">Ã¢Å“â€¦ {images.count()} Images Uploaded</strong><br><br>'
        html += '<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px;">'

        for image in images:
            if image.image:
                html += f'<div style="position: relative; border-radius: 8px; overflow: hidden; ' \
                        f'aspect-ratio: 1; border: 2px solid #BFDBFE;">' \
                        f'<img src="{image.image.url}" style="width: 100%; height: 100%; object-fit: cover;" />' \
                        f'<div style="position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,0.8); ' \
                        f'color: white; padding: 4px 8px; border-radius: 6px; font-size: 11px; font-weight: bold;">' \
                        f'Position {image.position}</div>' \
                        f'</div>'

        html += '</div></div>'

        return format_html(html)

    images_preview.short_description = 'Ã°Å¸â€“Â¼Ã¯Â¸Â Uploaded Images'


# Bulk form: multiple dates for one artist (list or range)
class ArtistAvailabilityBulkForm(forms.Form):
    artist = forms.ModelChoiceField(
        queryset=Service.objects.all().order_by('title'),
        label='Artist / Service',
        help_text='Select the artist (service) to update.',
    )
    use_date_range = forms.BooleanField(
        required=False,
        initial=False,
        label='Use date range',
        help_text='Toggle ON to fill dates from a range (From–To) instead of picking individual dates.',
    )
    date_from = forms.DateField(
        label='From date',
        widget=forms.DateInput(attrs={'type': 'date'}),
        required=False,
        help_text='First date in range (when "Use date range" is ON).',
    )
    date_to = forms.DateField(
        label='To date',
        widget=forms.DateInput(attrs={'type': 'date'}),
        required=False,
        help_text='Last date in range, inclusive (when "Use date range" is ON).',
    )
    dates = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={'id': 'id_dates'}),
        label='',
    )
    status = forms.ChoiceField(
        choices=ArtistAvailability.STATUS_CHOICES,
        initial=ArtistAvailability.STATUS_BLOCKED,
        label='Status',
        help_text='Blocked = grey, Booked = red, Available = clear.',
    )
    service_type = forms.CharField(
        max_length=50,
        initial='makeup',
        required=False,
        label='Service type',
        help_text='e.g. makeup, mehndi, haldi',
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Notes',
    )

    def clean(self):
        data = super().clean()
        use_range = data.get('use_date_range')
        date_from = data.get('date_from')
        date_to = data.get('date_to')
        dates_str = (data.get('dates') or '').strip()

        date_list = []
        if use_range:
            if not date_from or not date_to:
                self.add_error(None, forms.ValidationError('When using date range, both From date and To date are required.'))
                return data
            if date_from > date_to:
                self.add_error('date_to', forms.ValidationError('To date must be on or after From date.'))
                return data
            current = date_from
            while current <= date_to:
                date_list.append(current)
                current += timedelta(days=1)
        else:
            if not dates_str:
                self.add_error(None, forms.ValidationError('Please select at least one date, or turn on "Use date range".'))
                return data
            seen = set()
            for part in dates_str.replace('\n', ',').split(','):
                part = part.strip()
                if not part:
                    continue
                try:
                    d = datetime.strptime(part, '%Y-%m-%d').date()
                    if d not in seen:
                        seen.add(d)
                        date_list.append(d)
                except ValueError:
                    self.add_error('dates', forms.ValidationError(f'Invalid date format: "{part}". Use YYYY-MM-DD.'))
                    return data
            date_list.sort()

        if not date_list:
            self.add_error(None, forms.ValidationError('At least one date is required.'))
            return data
        data['date_list'] = date_list
        return data


@register(ArtistAvailability)
class ArtistAvailabilityAdmin(admin.ModelAdmin):
    change_list_template = 'admin/backend/artistavailability/change_list.html'
    list_display = ['id', 'artist', 'date', 'status', 'service_type', 'notes_short', 'created_at']
    list_filter = ['status', 'service_type', 'artist']
    search_fields = ['artist__title', 'artist__provider_name', 'notes']
    list_editable = ['status']
    date_hierarchy = 'date'
    ordering = ['-date', 'artist']
    autocomplete_fields = ['artist']

    fieldsets = (
        (None, {
            'fields': ('artist', 'date', 'service_type', 'status', 'notes')
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        extra = [
            path('bulk-add/', self.admin_site.admin_view(self.bulk_add_view), name='backend_artistavailability_bulk_add'),
        ]
        return extra + urls

    def bulk_add_view(self, request):
        if request.method == 'POST':
            form = ArtistAvailabilityBulkForm(request.POST)
            if form.is_valid():
                artist = form.cleaned_data['artist']
                date_list = form.cleaned_data['date_list']
                status = form.cleaned_data['status']
                service_type = form.cleaned_data['service_type'] or 'makeup'
                notes = form.cleaned_data['notes'] or ''
                created = 0
                for d in date_list:
                    obj, created_flag = ArtistAvailability.objects.update_or_create(
                        artist=artist,
                        date=d,
                        defaults={'status': status, 'service_type': service_type, 'notes': notes},
                    )
                    if created_flag:
                        created += 1
                total = len(date_list)
                self.message_user(
                    request,
                    f'Availability updated for {total} date(s).',
                    messages.SUCCESS,
                )
                return HttpResponseRedirect(reverse('admin:backend_artistavailability_changelist'))
        else:
            form = ArtistAvailabilityBulkForm()
        context = {
            **self.admin_site.each_context(request),
            'form': form,
            'title': 'Bulk add / update dates',
            'opts': self.model._meta,
        }
        return render(request, 'admin/backend/artistavailability_bulk_form.html', context)

    def notes_short(self, obj):
        if not obj.notes:
            return '-'
        return obj.notes[:40] + '...' if len(obj.notes) > 40 else obj.notes
    notes_short.short_description = 'Notes'


@register(ServicePageItem)
class ServicePageItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'position', 'category', 'viewtype']
    list_filter = ['viewtype', 'category']
    filter_horizontal = ['service_options']
    search_fields = ['title']
    search_help_text = "Search by title"
    ordering = ['category__position', 'position']


@register(ServiceBooking)
class ServiceBookingAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'user', 'service_option', 'booking_date', 'booking_time',
        'status', 'payment_status', 'total_amount', 'created_at'
    ]
    list_filter = ['status', 'payment_status', 'booking_date', 'created_at']
    search_fields = [
        'id', 'user__email', 'customer_name', 'customer_phone',
        'service_option__service__title'
    ]
    search_help_text = "Search by booking ID, user email, customer name, phone, or service"
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Booking Information', {
            'fields': ('user', 'service_option', 'booking_date', 'booking_time', 'duration')
        }),
        ('Customer Details', {
            'fields': ('customer_name', 'customer_phone', 'customer_address')
        }),
        ('Payment & Status', {
            'fields': ('total_amount', 'payment_status', 'status')
        }),
        ('Additional Info', {
            'fields': ('notes', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    ordering = ['-created_at']


# ============== VENDOR ADMIN ==============

from django import forms
from django.contrib import admin
from django.contrib.auth.hashers import make_password
from backend.models import Vendor


class VendorAdminForm(forms.ModelForm):
    """Custom form to handle password securely in the admin."""
    password = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Enter a password to set or update. Leave blank to keep the existing one."
    )

    class Meta:
        model = Vendor
        fields = '__all__'

    def save(self, commit=True):
        vendor = super().save(commit=False)
        raw_password = self.cleaned_data.get('password')
        # Hash the password only if a new one is entered (avoid double hashing)
        if raw_password and not raw_password.startswith('pbkdf2_'):
            vendor.password = make_password(raw_password)
        if commit:
            vendor.save()
        return vendor


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    """Admin panel configuration for Vendor model."""
    form = VendorAdminForm
    list_display = ['vendor_id', 'name', 'email', 'phone', 'trial_enabled', 'is_active', 'created_at']
    search_fields = ['vendor_id', 'name', 'email', 'phone', 'gst_number']
    list_filter = ['trial_enabled', 'is_active', 'created_at', 'updated_at']
    readonly_fields = ['vendor_id', 'created_at', 'updated_at']
    fieldsets = (
        ('Vendor Info', {
            'fields': ('vendor_id', 'name', 'email', 'phone', 'pincode', 'serviceable_locations', 'password')
        }),
        ('Business Details', {
            'fields': ('business_address', 'gst_number')
        }),
        ('Trial-at-Home', {
            'fields': ('trial_enabled',),
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def save_model(self, request, obj, form, change):
        """Ensure password is hashed when created/updated."""
        raw_password = form.cleaned_data.get('password')
        if raw_password and not raw_password.startswith('pbkdf2_'):
            obj.set_password(raw_password)
        super().save_model(request, obj, form, change)


@admin.register(ServiceVendor)
class ServiceVendorAdmin(admin.ModelAdmin):
    list_display = ['service_vendor_id', 'name', 'phone', 'area', 'pincode', 'is_active', 'created_at']
    search_fields = ['service_vendor_id', 'name', 'phone', 'area', 'pincode']
    list_filter = ['is_active', 'created_at', 'updated_at']
    readonly_fields = ['service_vendor_id', 'created_at', 'updated_at']
    filter_horizontal = ['service_subcategories']


@register(ServiceVendorToken)
class ServiceVendorTokenAdmin(admin.ModelAdmin):
    list_display = ['vendor', 'token_preview', 'created_at']
    readonly_fields = ['token', 'vendor', 'fcmtoken', 'created_at']
    search_fields = ['vendor__service_vendor_id', 'vendor__name', 'vendor__phone']

    def token_preview(self, obj):
        return f"{obj.token[:20]}..."

    token_preview.short_description = 'Token'

    def has_add_permission(self, request):
        return False



@register(VendorToken)
class VendorTokenAdmin(admin.ModelAdmin):
    list_display = ['vendor', 'token_preview', 'created_at']
    readonly_fields = ['token', 'vendor', 'fcmtoken', 'created_at']
    search_fields = ['vendor__vendor_id', 'vendor__name', 'vendor__email']

    def token_preview(self, obj):
        return f"{obj.token[:20]}..."

    token_preview.short_description = 'Token'

    def has_add_permission(self, request):
        return False


@admin.register(ProductBooking)
class ProductBookingAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'product_title',
        'booking_date_display',
        'rental_info_badge',
        'user_email',
        'quantity_booked',
        'status_badge',
        'vendor_contact_badge',
        'order_link',
        'created_at'
    ]

    list_filter = [
        'status',
        'rental_type',
        'booking_date',
        'created_at',
        'product'
    ]

    search_fields = [
        'product__title',
        'user__email',
        'user__phone',
        'order__id',
        'product__vendor__name',
        'product__vendor__phone'
    ]

    readonly_fields = [
        'created_at',
        'updated_at',
        'order_details',
        'vendor_full_info',
        'rental_period_display'
    ]

    fieldsets = (
        ('Booking Information', {
            'fields': ('product', 'product_option', 'booking_date', 'quantity_booked')
        }),
        ('Rental Details', {
            'fields': ('rental_type', 'rental_duration', 'rental_end_date', 'rental_period_display'),
            'classes': ('wide',),
        }),
        ('User & Order', {
            'fields': ('user', 'order', 'order_details')
        }),
        ('Vendor Information', {
            'fields': ('vendor_full_info',),
            'classes': ('wide',),
        }),
        ('Status', {
            'fields': ('status',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def product_title(self, obj):
        return obj.product.title

    product_title.short_description = 'Product'

    def booking_date_display(self, obj):
        return format_html(
            '<strong style="color: #6C5CE7;">{}</strong>',
            obj.booking_date.strftime('%b %d, %Y')
        )

    booking_date_display.short_description = 'Booking Date'

    def rental_info_badge(self, obj):
        if obj.rental_type == 'rent':
            duration_map = {
                '1_day': '1D', '2_days': '2D', '3_days': '3D',
                '7_days': '1W', '14_days': '2W', '30_days': '1M'
            }
            duration = duration_map.get(obj.rental_duration, 'N/A')
            return format_html(
                '<span style="background: #3B82F6; color: white; padding: 4px 8px; '
                'border-radius: 10px; font-size: 11px; font-weight: bold;">'
                ' RENT - {}</span>',
                duration
            )
        return format_html(
            '<span style="background: #10B981; color: white; padding: 4px 8px; '
            'border-radius: 10px; font-size: 11px; font-weight: bold;">'
            ' BUY</span>'
        )

    rental_info_badge.short_description = 'Rental Type'

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = 'Customer'

    def status_badge(self, obj):
        colors = {
            'PENDING': '#FFA500',
            'CONFIRMED': '#10B981',
            'CANCELLED': '#EF4444',
            'COMPLETED': '#3B82F6',
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; '
            'border-radius: 5px; font-weight: bold;">{}</span>',
            colors.get(obj.status, '#666666'),
            obj.status
        )

    status_badge.short_description = 'Status'

    def vendor_contact_badge(self, obj):
        vendor = obj.product.vendor
        if vendor:
            return format_html(
                '<div style="text-align: center;">'
                '<div style="font-weight: bold; font-size: 11px;">{}</div>'
                '<a href="tel:{}" style="color: #10B981; text-decoration: none; font-weight: bold;">'
                ' {}</a>'
                '</div>',
                vendor.name[:20],
                vendor.phone,
                vendor.phone
            )
        return '-'

    vendor_contact_badge.short_description = 'Vendor Contact'

    def order_link(self, obj):
        if obj.order:
            url = reverse('admin:backend_order_change', args=[obj.order.id])
            return format_html('<a href="{}">{}</a>', url, str(obj.order.id)[:8].upper())
        return '-'

    order_link.short_description = 'Order'

    def vendor_full_info(self, obj):
        """Display complete vendor information"""
        vendor = obj.product.vendor
        if vendor:
            return format_html(
                '<div style="padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); '
                'border-radius: 10px; color: white;">'
                '<h2 style="margin: 0 0 15px 0; color: white;"> Vendor Contact Details</h2>'
                '<div style="background: rgba(255,255,255,0.1); padding: 15px; border-radius: 8px;">'
                '<table style="width: 100%; color: white;">'
                '<tr><td style="padding: 8px 15px 8px 0; font-weight: bold; font-size: 14px;">Name:</td>'
                '<td style="font-size: 16px;"><strong>{}</strong></td></tr>'
                '<tr><td style="padding: 8px 15px 8px 0; font-weight: bold; font-size: 14px;">Vendor ID:</td>'
                '<td style="font-size: 16px;">{}</td></tr>'
                '<tr><td style="padding: 8px 15px 8px 0; font-weight: bold; font-size: 14px;">Phone:</td>'
                '<td><a href="tel:{}" style="color: #4ade80; font-weight: bold; font-size: 18px; '
                'text-decoration: none;"> {}</a></td></tr>'
                '<tr><td style="padding: 8px 15px 8px 0; font-weight: bold; font-size: 14px;">Email:</td>'
                '<td><a href="mailto:{}" style="color: #4ade80; text-decoration: none;">{}</a></td></tr>'
                '<tr><td style="padding: 8px 15px 8px 0; font-weight: bold; font-size: 14px;">Address:</td>'
                '<td style="font-size: 14px;">{}</td></tr>'
                '</table>'
                '</div>'
                '</div>',
                vendor.name,
                vendor.vendor_id,
                vendor.phone,
                vendor.phone,
                vendor.email,
                vendor.email,
                vendor.business_address or 'N/A'
            )
        return 'No vendor assigned'

    vendor_full_info.short_description = 'Vendor Contact Information'

    def rental_period_display(self, obj):
        if obj.rental_type == 'rent' and obj.rental_end_date:
            return format_html(
                '<div style="padding: 15px; background: #F0F9FF; border-radius: 8px; '
                'border-left: 4px solid #3B82F6;">'
                '<strong style="color: #3B82F6;">Rental Period</strong><br><br>'
                '<div style="display: flex; justify-content: space-between; margin-bottom: 10px;">'
                '<div><strong>Start:</strong> {}</div>'
                '<div><strong>End:</strong> {}</div>'
                '</div>'
                '<div style="margin-top: 10px; padding: 8px; background: white; border-radius: 4px;">'
                '<strong>Duration:</strong> {} days'
                '</div>'
                '</div>',
                obj.booking_date.strftime('%B %d, %Y'),
                obj.rental_end_date.strftime('%B %d, %Y'),
                (obj.rental_end_date - obj.booking_date).days + 1
            )
        return 'Purchase (No rental period)'

    rental_period_display.short_description = 'Rental Period'

    def order_details(self, obj):
        if obj.order:
            return format_html(
                '<div style="padding: 10px; background: #f5f5f5; border-radius: 5px;">'
                '<strong>Order ID:</strong> {}<br>'
                '<strong>Total Amount:</strong> Rs {}<br>'
                '<strong>Payment Mode:</strong> {}<br>'
                '<strong>Status:</strong> {}'
                '</div>',
                str(obj.order.id)[:8].upper(),
                obj.order.tx_amount,
                obj.order.payment_mode,
                obj.order.tx_status
            )
        return 'No order associated'

    order_details.short_description = 'Order Details'

    actions = ['confirm_bookings', 'cancel_bookings', 'mark_completed']

    def confirm_bookings(self, request, queryset):
        """Confirm selected bookings"""
        confirmed_count = queryset.filter(status='PENDING').update(status='CONFIRMED')
        self.message_user(request, f'{confirmed_count} booking(s) confirmed.')

    confirm_bookings.short_description = "Confirm selected bookings"

    def cancel_bookings(self, request, queryset):
        """Cancel selected bookings"""
        cancelled_count = queryset.filter(status__in=['PENDING', 'CONFIRMED']).update(status='CANCELLED')
        self.message_user(request, f'{cancelled_count} booking(s) cancelled.')

    cancel_bookings.short_description = "Cancel selected bookings"

    def mark_completed(self, request, queryset):
        """Mark bookings as completed"""
        completed_count = queryset.filter(status='CONFIRMED').update(status='COMPLETED')
        self.message_user(request, f'{completed_count} booking(s) marked as completed.')

    mark_completed.short_description = "Mark as completed"


# Enhanced CartItem Admin
@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'user',
        'product_option',
        'rental_type_badge',
        'quantity',
        'rental_price',
        'selected_date',
        'created_at'
    ]

    list_filter = ['rental_type', 'rental_duration', 'created_at']
    search_fields = ['user__email', 'product_option__product__title']
    readonly_fields = ['created_at', 'rental_info_display']

    fieldsets = (
        ('Cart Item', {
            'fields': ('user', 'product_option', 'quantity')
        }),
        ('Rental Details', {
            'fields': ('rental_type', 'rental_duration', 'rental_price', 'selected_date', 'rental_info_display')
        }),
        ('Timestamp', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )

    def rental_type_badge(self, obj):
        colors = {'rent': '#3B82F6', 'buy': '#10B981'}
        return format_html(
            '<span style="background: {}; color: white; padding: 4px 8px; '
            'border-radius: 10px; font-size: 11px; font-weight: bold;">{}</span>',
            colors.get(obj.rental_type, '#666'),
            obj.rental_type.upper()
        )

    rental_type_badge.short_description = 'Type'

    def rental_info_display(self, obj):
        if obj.rental_type == 'rent':
            return format_html(
                '<div style="padding: 10px; background: #E3F2FD; border-radius: 5px;">'
                '<strong>Rental Details:</strong><br>'
                'Duration: {}<br>'
                'Price: Rs {}<br>'
                'Date: {}'
                '</div>',
                obj.rental_duration.replace('_', ' ').title(),
                obj.rental_price,
                obj.selected_date.strftime('%B %d, %Y') if obj.selected_date else 'Not selected'
            )
        return 'Purchase'

    rental_info_display.short_description = 'Rental Info'


# Add to backend/admin.py

from django.contrib import messages


@register(ServiceableLocation)
class ServiceableLocationAdmin(admin.ModelAdmin):
    list_display = [
        'pincode', 'area_name', 'city', 'state',
        'is_active', 'rent_available', 'service_available',
        'delivery_charge', 'coordinates_display', 'created_at'
    ]
    list_filter = ['is_active', 'rent_available', 'service_available', 'city']
    search_fields = ['pincode', 'area_name', 'city']
    list_editable = ['is_active', 'rent_available', 'service_available']

    readonly_fields = ['coordinates_display', 'created_at', 'updated_at']

    fieldsets = (
        ('Location Details', {
            'fields': ('pincode', 'area_name', 'city', 'state'),
            'description': ' Enter pincode and area name - coordinates will be fetched automatically on save'
        }),
        ('Coordinates', {
            'fields': ('latitude', 'longitude', 'coordinates_display'),
            'classes': ('collapse',),
            'description': ' Auto-filled from pincode and area name. You can also enter manually if needed.'
        }),
        ('Service Availability', {
            'fields': ('is_active', 'rent_available', 'service_available')
        }),
        ('Delivery Settings', {
            'fields': ('delivery_charge', 'delivery_time')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def coordinates_display(self, obj):
        """Display coordinates in a formatted way"""
        if obj.latitude and obj.longitude:
            google_maps_url = f"https://www.google.com/maps?q={obj.latitude},{obj.longitude}"
            return format_html(
                '<div style="padding: 10px; background: #E8F5E9; border-radius: 8px;">'
                '<strong style="color: #2E7D32;"> Coordinates Found</strong><br>'
                '<span style="color: #555;">Lat: <strong>{}</strong></span><br>'
                '<span style="color: #555;">Lng: <strong>{}</strong></span><br>'
                '<a href="{}" target="_blank" style="color: #1976D2; text-decoration: none;">'
                ' View on Google Maps</a>'
                '</div>',
                obj.latitude, obj.longitude, google_maps_url
            )
        return format_html(
            '<div style="padding: 10px; background: #FFF3E0; border-radius: 8px;">'
            '<strong style="color: #F57C00;">  No Coordinates</strong><br>'
            '<span style="color: #555;">Will be auto-fetched on save</span>'
            '</div>'
        )

    coordinates_display.short_description = 'Current Coordinates'

    def save_model(self, request, obj, form, change):
        """Auto-fetch coordinates if not provided"""
        from backend.utils import get_coordinates_from_location

        # Check if coordinates are empty or need updating
        should_fetch = False

        if not obj.latitude or not obj.longitude:
            should_fetch = True
            reason = "no coordinates provided"
        elif change:  # If updating existing record
            # Check if pincode or area changed
            old_obj = ServiceableLocation.objects.get(pk=obj.pk)
            if (old_obj.pincode != obj.pincode or
                    old_obj.area_name != obj.area_name or
                    old_obj.city != obj.city):
                should_fetch = True
                reason = "location details changed"

        if should_fetch:
            print(f"\n Auto-fetching coordinates ({reason})...")
            print(f" Location: {obj.area_name}, {obj.city}, {obj.state} - {obj.pincode}")

            lat, lng = get_coordinates_from_location(
                pincode=obj.pincode,
                area_name=obj.area_name,
                city=obj.city,
                state=obj.state
            )

            if lat and lng:
                obj.latitude = lat
                obj.longitude = lng
                messages.success(
                    request,
                    f' Successfully fetched coordinates: {lat}, {lng}'
                )
                print(f" Coordinates saved: {lat}, {lng}\n")
            else:
                messages.warning(
                    request,
                    f'  Could not fetch coordinates for {obj.area_name}, {obj.pincode}. '
                    'Please enter manually if needed.'
                )
                print(f"  Could not fetch coordinates\n")
        else:
            print(f" Using existing coordinates: {obj.latitude}, {obj.longitude}")

        super().save_model(request, obj, form, change)

    actions = ['enable_all_services', 'disable_all_services', 'bulk_add_pincodes', 'fetch_coordinates_for_selected']

    def enable_all_services(self, request, queryset):
        queryset.update(is_active=True, rent_available=True, service_available=True)
        self.message_user(request, f" {queryset.count()} location(s) enabled.")

    enable_all_services.short_description = "Enable all services"

    def disable_all_services(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, f" {queryset.count()} location(s) disabled.")

    disable_all_services.short_description = "Disable services"

    def fetch_coordinates_for_selected(self, request, queryset):
        """Fetch coordinates for selected locations"""
        from backend.utils import get_coordinates_from_location

        updated_count = 0
        failed_count = 0

        for location in queryset:
            print(f"\n Fetching coordinates for: {location.area_name}, {location.pincode}")

            lat, lng = get_coordinates_from_location(
                pincode=location.pincode,
                area_name=location.area_name,
                city=location.city,
                state=location.state
            )

            if lat and lng:
                location.latitude = lat
                location.longitude = lng
                location.save()
                updated_count += 1
                print(f" Updated: {lat}, {lng}")
            else:
                failed_count += 1
                print(f" Failed to fetch coordinates")

        if updated_count > 0:
            self.message_user(
                request,
                f' Successfully updated coordinates for {updated_count} location(s).'
            )

        if failed_count > 0:
            self.message_user(
                request,
                f'  Failed to fetch coordinates for {failed_count} location(s).',
                level=messages.WARNING
            )

    fetch_coordinates_for_selected.short_description = "Fetch coordinates for selected"

    def bulk_add_pincodes(self, request, queryset):
        """Bulk add Agar Malwa pincodes with auto-coordinates"""
        from backend.utils import get_coordinates_from_location

        pincodes_data = [
            ('465441', 'Agar Malwa'),
            ('465447', 'Susner'),
            ('465550', 'Barod'),
            ('465445', 'Nalkheda'),
            ('465449', 'Soyat Kalan'),
            ('465230', 'Kanad'),
            ('465441', 'Biyana'),
            ('465441', 'Bijanagri'),
            ('465445', 'Bhandavad'),
            ('465445', 'Bijnakhedi'),
            ('465447', 'Chhapariya'),
            ('465449', 'Ghosla'),
            ('465441', 'Sadol'),
            ('465441', 'Sondani'),
            ('465441', 'Thandla'),
            ('465445', 'Ghosunda'),
            ('465447', 'Dabikhedi'),
            ('465449', 'Bhaisoda'),
            ('465441', 'Kachnariya'),
            ('465445', 'Tajpura'),
        ]

        created = 0
        for pincode, area in pincodes_data:
            location, created_flag = ServiceableLocation.objects.get_or_create(
                pincode=pincode,
                area_name=area,
                defaults={
                    'city': 'Agar Malwa',
                    'state': 'Madhya Pradesh',
                    'is_active': True,
                    'rent_available': True,
                    'service_available': True,
                }
            )

            if created_flag:
                # Fetch coordinates for new location
                print(f"\n New location: {area}, {pincode}")
                lat, lng = get_coordinates_from_location(
                    pincode=pincode,
                    area_name=area,
                    city='Agar Malwa',
                    state='Madhya Pradesh'
                )

                if lat and lng:
                    location.latitude = lat
                    location.longitude = lng
                    location.save()
                    print(f" Coordinates saved: {lat}, {lng}")
                else:
                    print(f"  Could not fetch coordinates")

                created += 1

        self.message_user(request, f" Added {created} new pincode(s) with coordinates")

    bulk_add_pincodes.short_description = "Bulk Add Agar Malwa Pincodes"

    class Media:
        js = ('admin/js/serviceable_location_auto_fetch.js',)


@register(CategoryAvailability)
class CategoryAvailabilityAdmin(admin.ModelAdmin):
    list_display = ['category', 'location', 'is_available', 'priority', 'created_at']
    list_filter = ['is_available', 'location', 'category']
    search_fields = ['category__name', 'location__pincode', 'location__area_name']
    list_editable = ['is_available', 'priority']

    actions = ['enable_for_all_pincodes', 'disable_for_all_pincodes', 'bulk_create_availabilities']

    #  Custom change list template
    change_list_template = 'admin/category_availability_changelist.html'

    def changelist_view(self, request, extra_context=None):
        """Add bulk creation form to change list"""
        extra_context = extra_context or {}

        #  FIX: Handle POST request properly
        if request.method == 'POST' and 'bulk_create' in request.POST:
            self.handle_bulk_create(request)
            #  Redirect back to changelist after handling POST
            from django.http import HttpResponseRedirect
            return HttpResponseRedirect(request.path)

        extra_context['bulk_form'] = BulkCategoryAvailabilityForm()
        return super().changelist_view(request, extra_context=extra_context)

    def handle_bulk_create(self, request):
        """Handle bulk category availability creation"""
        form = BulkCategoryAvailabilityForm(request.POST)

        if form.is_valid():
            categories = form.cleaned_data['categories']
            location = form.cleaned_data['location']
            is_available = form.cleaned_data['is_available']
            priority = form.cleaned_data.get('priority', 0)

            created_count = 0
            updated_count = 0
            skipped_count = 0

            for category in categories:
                try:
                    obj, created = CategoryAvailability.objects.update_or_create(
                        category=category,
                        location=location,
                        defaults={
                            'is_available': is_available,
                            'priority': priority
                        }
                    )
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                except Exception as e:
                    skipped_count += 1
                    print(f" Error creating availability for {category.name}: {e}")

            #  Show success message
            message_parts = []
            if created_count > 0:
                message_parts.append(f" Created {created_count} new")
            if updated_count > 0:
                message_parts.append(f" Updated {updated_count} existing")
            if skipped_count > 0:
                message_parts.append(f" Skipped {skipped_count}")

            if message_parts:
                self.message_user(
                    request,
                    " | ".join(message_parts) + f" availability(ies) for {location.area_name}",
                    messages.SUCCESS if skipped_count == 0 else messages.WARNING
                )
            else:
                self.message_user(
                    request,
                    " No changes made",
                    messages.INFO
                )
        else:
            #  Show validation errors
            errors = []
            for field, error_list in form.errors.items():
                errors.append(f"{field}: {', '.join(error_list)}")

            self.message_user(
                request,
                f" Form validation failed: {' | '.join(errors)}",
                messages.ERROR
            )

    def enable_for_all_pincodes(self, request, queryset):
        """Enable selected categories for all serviceable pincodes"""
        locations = ServiceableLocation.objects.filter(is_active=True)
        created_count = 0
        updated_count = 0

        for item in queryset:
            for location in locations:
                obj, created = CategoryAvailability.objects.get_or_create(
                    category=item.category,
                    location=location,
                    defaults={'is_available': True, 'priority': 0}
                )
                if created:
                    created_count += 1
                elif not obj.is_available:
                    obj.is_available = True
                    obj.save()
                    updated_count += 1

        self.message_user(
            request,
            f" Processed {queryset.count()} categories for {locations.count()} locations "
            f"({created_count} created, {updated_count} updated)"
        )

    enable_for_all_pincodes.short_description = "Enable for all pincodes"

    def disable_for_all_pincodes(self, request, queryset):
        count = queryset.update(is_available=False)
        self.message_user(request, f" Disabled {count} availability(ies)")

    disable_for_all_pincodes.short_description = "Disable selected"

    def bulk_create_availabilities(self, request, queryset):
        """Show statistics about selected items"""
        categories = set()
        locations = set()

        for item in queryset:
            categories.add(item.category)
            locations.add(item.location)

        self.message_user(
            request,
            f" Selected: {len(categories)} categories  {len(locations)} locations",
            messages.INFO
        )

    bulk_create_availabilities.short_description = "Show statistics"


@register(PageItemAvailability)
class PageItemAvailabilityAdmin(admin.ModelAdmin):
    list_display = ['page_item', 'location', 'is_available', 'priority', 'created_at']
    list_filter = ['is_available', 'location', 'page_item__category']
    search_fields = ['page_item__title', 'location__pincode', 'location__area_name']
    list_editable = ['is_available', 'priority']


@register(ServiceCategoryAvailability)
class ServiceCategoryAvailabilityAdmin(admin.ModelAdmin):
    list_display = ['service_category', 'location', 'is_available', 'priority', 'created_at']
    list_filter = ['is_available', 'location', 'service_category']
    search_fields = ['service_category__name', 'location__pincode', 'location__area_name']
    list_editable = ['is_available', 'priority']


class BulkCategoryAvailabilityForm(forms.Form):
    """Form for creating multiple category availabilities at once"""
    categories = forms.ModelMultipleChoiceField(
        queryset=Category.objects.all().order_by('name'),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        help_text=" Select multiple categories to enable for this location"
    )
    location = forms.ModelChoiceField(
        queryset=ServiceableLocation.objects.filter(is_active=True).order_by('pincode'),
        required=True,
        help_text=" Select the location"
    )
    is_available = forms.BooleanField(
        initial=True,
        required=False,
        help_text=" Make selected categories available"
    )
    priority = forms.IntegerField(
        initial=0,
        required=False,
        help_text=" Display priority (higher = shown first)"
    )


# admin.py - Update the form class

# admin.py - Update the form to be completely optional

class HomePageItemAdminForm(forms.ModelForm):
    """
    Ã¢Å“â€¦ COMPLETELY OPTIONAL FORM - No validation errors
    All fields are optional to allow saving drafts
    """

    class Meta:
        model = HomePageItem
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Ã¢Å“â€¦ Make ALL fields optional
        for field_name, field in self.fields.items():
            field.required = False

        # Add helpful placeholders
        self.fields['title'].widget.attrs.update({
            'placeholder': 'Enter a title (optional)',
            'style': 'width: 100%;'
        })
        self.fields['position'].widget.attrs.update({
            'placeholder': '0',
            'min': '0',
            'max': '9999'
        })

        # Add help text
        self.fields['title'].help_text = 'Ã°Å¸â€œÂ Display title for this home section'
        self.fields['position'].help_text = 'Ã°Å¸â€Â¢ Lower numbers appear first (0-9999)'
        self.fields['viewtype'].help_text = 'Ã°Å¸â€˜ÂÃ¯Â¸Â How items are displayed: Banner (1), Swiper (2), Grid (3)'

    def clean(self):
        """
        Ã¢Å“â€¦ NO VALIDATION - Accept everything
        Just return cleaned data without raising errors
        """
        cleaned_data = super().clean()

        # Ensure position is within valid range
        position = cleaned_data.get('position', 0)
        if position is not None:
            cleaned_data['position'] = max(0, min(9999, position))

        return cleaned_data


# ============================================================================
# ADMIN CLASS
# ============================================================================

@admin.register(HomePageItem)
class HomePageItemAdmin(admin.ModelAdmin):
    form = HomePageItemAdminForm

    list_display = [
        'title_badge',
        'item_type_badge',
        'position',
        'viewtype_badge',
        'category_display',
        'items_count_badge',
        'location_badge',
        'is_active',
        'created_at'
    ]

    list_filter = [
        'item_type',
        'viewtype',
        'is_active',
        'show_in_all_locations',
        'created_at',
        'category',
        'service_category',
    ]

    search_fields = [
        'title',
        'category__name',
        'service_category__name',
    ]

    filter_horizontal = [
        'product_options',
        'service_options',
        'specific_locations'
    ]

    list_editable = ['position', 'is_active']

    readonly_fields = [
        'items_preview',
        'location_preview',
        'validation_warnings',
        'created_at',
        'updated_at'
    ]

    fieldsets = (
        ('Ã°Å¸â€œâ€¹ Basic Information', {
            'fields': ('title', 'subtitle', 'item_type', 'position', 'viewtype', 'image'),
            'description': 'Ã¢Å“â€¦ All fields are optional - fill what you need'
        }),
        ('Ã°Å¸â€œÂ¦ Category & Items', {
            'fields': ('category', 'service_category', 'product_options', 'service_options'),
            'description': 'Ã¢Å“â€¦ Optional - Add items when ready. Select category based on item type.'
        }),
        ('Ã°Å¸â€œÂ Location Settings', {
            'fields': ('show_in_all_locations', 'specific_locations', 'location_preview'),
            'description': 'Ã¢Å“â€¦ Control where this section appears (check "show_in_all_locations" or select specific ones)'
        }),
        ('Ã¢Å¡ Ã¯Â¸Â Validation Status', {
            'fields': ('is_active', 'validation_warnings'),
            'classes': ('collapse',),
            'description': 'Ã¢Å“â€¦ Check for any warnings (not blocking saves)'
        }),
        ('Ã°Å¸â€¢Â Timestamps & Preview', {
            'fields': ('items_preview', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    ordering = ['position', '-created_at']

    # ============================================================================
    # LIST DISPLAY METHODS
    # ============================================================================

    def title_badge(self, obj):
        """Display title with icon"""
        icon = 'Ã°Å¸Â ' if obj.item_type == 'rent' else 'Ã°Å¸â€Â§'
        title = obj.title or 'Untitled'
        return format_html(
            '<div style="font-weight: bold; font-size: 13px;">{} {}</div>',
            icon, title
        )

    title_badge.short_description = 'Ã°Å¸â€œÅ’ Title'

    def item_type_badge(self, obj):
        """Display item type with color badge"""
        colors = {
            'rent': '#3B82F6',
            'service': '#10B981',
        }
        icons = {
            'rent': 'Ã°Å¸Â ',
            'service': 'Ã°Å¸â€Â§',
        }
        return format_html(
            '<span style="background: {}; color: white; padding: 5px 12px; '
            'border-radius: 15px; font-size: 11px; font-weight: bold; white-space: nowrap;">'
            '{} {}</span>',
            colors.get(obj.item_type, '#666'),
            icons.get(obj.item_type, ''),
            obj.get_item_type_display().upper()
        )

    item_type_badge.short_description = 'Ã°Å¸ÂÂ·Ã¯Â¸Â Type'

    def viewtype_badge(self, obj):
        """Display view type with icon"""
        viewtype_display = {
            1: 'Ã°Å¸â€œÂ± Banner',
            2: 'Ã¢â€ â€Ã¯Â¸Â Swiper',
            3: 'Ã°Å¸â€œÅ  Grid',
        }
        badge = viewtype_display.get(obj.viewtype, 'Ã¢Ââ€œ Unknown')
        return format_html(
            '<span style="font-size: 12px; font-weight: 500;">{}</span>',
            badge
        )

    viewtype_badge.short_description = 'Ã°Å¸â€˜ÂÃ¯Â¸Â View'

    def category_display(self, obj):
        """Display associated category"""
        if obj.item_type == 'rent' and obj.category:
            return format_html(
                '<span style="color: #3B82F6; font-weight: 500;">Ã°Å¸Â  {}</span>',
                obj.category.name
            )
        elif obj.item_type == 'service' and obj.service_category:
            return format_html(
                '<span style="color: #10B981; font-weight: 500;">Ã°Å¸â€Â§ {}</span>',
                obj.service_category.name
            )
        return format_html('<span style="color: #EF4444;">Ã¢Å¡ Ã¯Â¸Â Not Set</span>')

    category_display.short_description = 'Ã°Å¸â€œâ€š Category'

    def items_count_badge(self, obj):
        """Display count of items"""
        count = obj.get_items_count()

        if count == 0:
            return format_html(
                '<span style="background: #EF4444; color: white; padding: 4px 10px; '
                'border-radius: 10px; font-weight: bold; font-size: 11px;">Ã¢Å¡ Ã¯Â¸Â 0 items</span>'
            )
        elif count < 3:
            return format_html(
                '<span style="background: #F59E0B; color: white; padding: 4px 10px; '
                'border-radius: 10px; font-weight: bold; font-size: 11px;">Ã¢Å¡ Ã¯Â¸Â {} items</span>',
                count
            )
        else:
            return format_html(
                '<span style="background: #10B981; color: white; padding: 4px 10px; '
                'border-radius: 10px; font-weight: bold; font-size: 11px;">Ã¢Å“â€¦ {} items</span>',
                count
            )

    items_count_badge.short_description = 'Ã°Å¸â€œÂ¦ Items'

    def location_badge(self, obj):
        """Display location availability"""
        if obj.show_in_all_locations:
            return format_html(
                '<span style="color: #10B981; font-weight: bold; font-size: 12px;">Ã°Å¸Å’Â All Locations</span>'
            )
        else:
            count = obj.specific_locations.count()
            if count == 0:
                return format_html(
                    '<span style="color: #EF4444; font-weight: bold; font-size: 12px;">Ã¢Å¡ Ã¯Â¸Â No Locations</span>'
                )
            return format_html(
                '<span style="color: #F59E0B; font-weight: bold; font-size: 12px;">Ã°Å¸â€œÂ {} Locations</span>',
                count
            )

    location_badge.short_description = 'Ã°Å¸â€œÂ Locations'

    # ============================================================================
    # READONLY FIELD METHODS
    # ============================================================================

    def validation_warnings(self, obj):
        """Show warnings about incomplete configuration"""
        if not obj.pk:
            return format_html(
                '<div style="padding: 15px; background: #E0E7FF; border-radius: 8px;">'
                '<p style="margin: 0; color: #3730A3;">Ã°Å¸â€™Â¡ Save first to see validation status</p>'
                '</div>'
            )

        warnings = []

        # Check for missing title
        if not obj.title:
            warnings.append("Ã¢Å¡ Ã¯Â¸Â No title set - consider adding a descriptive title")

        # Check item type configuration
        if obj.item_type == 'rent':
            if not obj.category:
                warnings.append("Ã¢Å¡ Ã¯Â¸Â No category selected for rent items")
            if obj.product_options.count() == 0:
                warnings.append("Ã¢Å¡ Ã¯Â¸Â No product options selected - section will be empty")
        elif obj.item_type == 'service':
            if not obj.service_category:
                warnings.append("Ã¢Å¡ Ã¯Â¸Â No service category selected")
            if obj.service_options.count() == 0:
                warnings.append("Ã¢Å¡ Ã¯Â¸Â No service options selected - section will be empty")

        # Check location settings
        if not obj.show_in_all_locations and obj.specific_locations.count() == 0:
            warnings.append("Ã¢Å¡ Ã¯Â¸Â No locations selected - item won't be visible to any users")

        # Check if inactive
        if not obj.is_active:
            warnings.append("Ã¢â€žÂ¹Ã¯Â¸Â Item is inactive - won't appear on home screen")

        if warnings:
            html = '<div style="padding: 20px; background: #FEF3C7; border-radius: 12px; border-left: 4px solid #F59E0B;">'
            html += '<h3 style="margin: 0 0 15px 0; color: #92400E; font-size: 16px;">Ã¢Å¡ Ã¯Â¸Â Configuration Warnings</h3>'
            html += '<ul style="margin: 0; padding-left: 20px; color: #78350F; line-height: 1.8;">'
            for warning in warnings:
                html += f'<li style="margin-bottom: 5px;">{warning}</li>'
            html += '</ul>'
            html += '<div style="margin-top: 15px; padding: 12px; background: rgba(255,255,255,0.5); border-radius: 6px;">'
            html += '<strong style="color: #92400E;">Ã°Å¸â€™Â¡ Note:</strong> '
            html += '<span style="color: #78350F;">These are just warnings - item saved successfully. '
            html += 'Complete the configuration when ready.</span>'
            html += '</div></div>'
            return format_html(html)

        return format_html(
            '<div style="padding: 20px; background: #ECFDF5; border-radius: 12px; border-left: 4px solid #10B981;">'
            '<h3 style="margin: 0 0 10px 0; color: #065F46; font-size: 16px;">Ã¢Å“â€¦ All Good!</h3>'
            '<p style="margin: 0; color: #065F46; line-height: 1.6;">'
            'Configuration looks complete. This home page section is ready to be displayed.'
            '</p></div>'
        )

    validation_warnings.short_description = 'Ã¢Å¡ Ã¯Â¸Â Configuration Status'

    def items_preview(self, obj):
        """Display preview of associated items"""
        if obj.item_type == 'rent':
            items = obj.product_options.select_related('product').all()[:15]

            if not items:
                return format_html(
                    '<div style="padding: 20px; background: #FEE2E2; border-radius: 8px; '
                    'border-left: 4px solid #EF4444; text-align: center;">'
                    '<p style="color: #991B1B; margin: 0; font-weight: bold;">Ã¢Å¡ Ã¯Â¸Â No products selected</p>'
                    '<p style="color: #7F1D1D; margin: 10px 0 0 0; font-size: 13px;">'
                    'Add product options using the "Product options" field above'
                    '</p></div>'
                )

            items_html = '<div style="padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); ' \
                         'border-radius: 12px; color: white;">'
            items_html += f'<h3 style="margin: 0 0 15px 0; color: white; font-size: 18px;">Ã°Å¸Â  Selected Products ({items.count()})</h3>'

            items_html += '<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px;">'

            for item in items:
                stock_color = '#10B981' if item.quantity > 10 else '#F59E0B' if item.quantity > 0 else '#EF4444'
                items_html += f'''
                <div style="background: rgba(255,255,255,0.1); padding: 12px; border-radius: 8px; 
                     border: 1px solid rgba(255,255,255,0.2);">
                    <div style="font-weight: bold; margin-bottom: 5px; font-size: 13px;">
                        {item.product.title[:30]}{'...' if len(item.product.title) > 30 else ''}
                    </div>
                    {f'<div style="color: #FFD700; margin-bottom: 5px; font-size: 11px;">Ã°Å¸â€œÂ¦ {item.option}</div>' if item.option else ''}
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 8px;">
                        <span style="font-size: 11px; color: rgba(255,255,255,0.8);">Stock:</span>
                        <span style="background: {stock_color}; padding: 3px 8px; border-radius: 10px; 
                               font-size: 10px; font-weight: bold;">{item.quantity}</span>
                    </div>
                </div>
                '''

            items_html += '</div>'

            total = obj.product_options.count()
            if total > 15:
                items_html += f'<div style="margin-top: 15px; padding: 10px; background: rgba(255,255,255,0.1); ' \
                              f'border-radius: 6px; text-align: center; color: rgba(255,255,255,0.9);">' \
                              f'... and <strong>{total - 15}</strong> more products</div>'

            items_html += '</div>'
            return format_html(items_html)

        else:  # service
            items = obj.service_options.select_related('service').all()[:15]

            if not items:
                return format_html(
                    '<div style="padding: 20px; background: #FEE2E2; border-radius: 8px; '
                    'border-left: 4px solid #EF4444; text-align: center;">'
                    '<p style="color: #991B1B; margin: 0; font-weight: bold;">Ã¢Å¡ Ã¯Â¸Â No services selected</p>'
                    '<p style="color: #7F1D1D; margin: 10px 0 0 0; font-size: 13px;">'
                    'Add service options using the "Service options" field above'
                    '</p></div>'
                )

            items_html = '<div style="padding: 20px; background: linear-gradient(135deg, #10b981 0%, #059669 100%); ' \
                         'border-radius: 12px; color: white;">'
            items_html += f'<h3 style="margin: 0 0 15px 0; color: white; font-size: 18px;">Ã°Å¸â€Â§ Selected Services ({items.count()})</h3>'

            items_html += '<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px;">'

            for item in items:
                available_badge = 'Ã¢Å“â€¦' if item.available else 'Ã¢ÂÅ’'
                available_color = '#10B981' if item.available else '#EF4444'

                items_html += f'''
                <div style="background: rgba(255,255,255,0.1); padding: 12px; border-radius: 8px; 
                     border: 1px solid rgba(255,255,255,0.2);">
                    <div style="font-weight: bold; margin-bottom: 5px; font-size: 13px;">
                        {item.service.title[:30]}{'...' if len(item.service.title) > 30 else ''}
                    </div>
                    <div style="color: #FFD700; margin-bottom: 5px; font-size: 11px;">Ã°Å¸â€Â§ {item.option_name}</div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 8px;">
                        <span style="font-size: 14px; font-weight: bold;">Ã¢â€šÂ¹{item.price}</span>
                        <span style="background: {available_color}; padding: 3px 8px; border-radius: 10px; 
                               font-size: 10px;">{available_badge}</span>
                    </div>
                    {f'<div style="margin-top: 5px; font-size: 10px; color: rgba(255,255,255,0.7);">Ã¢ÂÂ±Ã¯Â¸Â {item.duration}</div>' if item.duration else ''}
                </div>
                '''

            items_html += '</div>'

            total = obj.service_options.count()
            if total > 15:
                items_html += f'<div style="margin-top: 15px; padding: 10px; background: rgba(255,255,255,0.1); ' \
                              f'border-radius: 6px; text-align: center; color: rgba(255,255,255,0.9);">' \
                              f'... and <strong>{total - 15}</strong> more services</div>'

            items_html += '</div>'
            return format_html(items_html)

    items_preview.short_description = 'Ã°Å¸â€˜â‚¬ Items Preview'

    def location_preview(self, obj):
        """Display location availability details"""
        if obj.show_in_all_locations:
            locations = ServiceableLocation.objects.filter(is_active=True)
            count = locations.count()

            html = '<div style="padding: 20px; background: #ECFDF5; border-radius: 12px; border-left: 4px solid #10B981;">'
            html += f'<h3 style="margin: 0 0 15px 0; color: #10B981; font-size: 18px;">Ã°Å¸Å’Â Available in ALL {count} Locations</h3>'
            html += '<p style="margin: 0 0 15px 0; color: #065F46; line-height: 1.6;">' \
                    'This item will be shown to users in all serviceable areas.</p>'

            # Show first 10 locations as preview
            if locations.exists():
                html += '<div style="margin-top: 15px; padding: 15px; background: white; border-radius: 8px; ' \
                        'border: 1px solid #D1FAE5;">'
                html += '<strong style="font-size: 13px; color: #065F46;">Ã°Å¸â€œÂ Sample Locations:</strong>'
                html += '<div style="margin-top: 10px; display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px;">'

                for loc in locations[:10]:
                    html += f'''
                    <div style="padding: 8px; background: #F0FDF4; border-radius: 6px; font-size: 11px;">
                        <div style="font-weight: bold; color: #065F46;">{loc.area_name}</div>
                        <div style="color: #059669;">{loc.pincode} Ã¢â‚¬Â¢ {loc.city}</div>
                    </div>
                    '''

                html += '</div>'

                if count > 10:
                    html += f'<div style="margin-top: 10px; text-align: center; color: #059669; font-size: 12px; font-style: italic;">' \
                            f'... and {count - 10} more locations</div>'

                html += '</div>'

            html += '</div>'
            return format_html(html)

        else:
            locations = obj.specific_locations.filter(is_active=True)
            count = locations.count()

            if count == 0:
                return format_html(
                    '<div style="padding: 20px; background: #FEE2E2; border-radius: 12px; border-left: 4px solid #EF4444;">'
                    '<h3 style="margin: 0 0 10px 0; color: #EF4444; font-size: 18px;">Ã¢Å¡ Ã¯Â¸Â No Locations Selected</h3>'
                    '<p style="margin: 0; color: #991B1B; line-height: 1.6;">'
                    'This item won\'t be visible to any users! '
                    'Either check "Show in all locations" or select specific locations above.'
                    '</p></div>'
                )

            html = '<div style="padding: 20px; background: #FEF3C7; border-radius: 12px; border-left: 4px solid #F59E0B;">'
            html += f'<h3 style="margin: 0 0 15px 0; color: #F59E0B; font-size: 18px;">Ã°Å¸â€œÂ Available in {count} Specific Locations</h3>'

            html += '<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px;">'

            for loc in locations[:20]:
                html += f'''
                <div style="padding: 12px; background: white; border-radius: 8px; 
                     border: 1px solid #FDE68A; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                    <div style="font-weight: bold; color: #92400E; margin-bottom: 5px; font-size: 13px;">
                        {loc.area_name}
                    </div>
                    <div style="color: #78350F; font-size: 11px;">
                        Ã°Å¸â€œÂ® {loc.pincode}
                    </div>
                    <div style="color: #78350F; font-size: 11px;">
                        Ã°Å¸â€œÂ {loc.city}, {loc.state}
                    </div>
                </div>
                '''

            html += '</div>'

            if count > 20:
                html += f'<div style="margin-top: 15px; padding: 10px; background: rgba(255,255,255,0.5); ' \
                        f'border-radius: 6px; text-align: center; color: #78350F; font-style: italic;">' \
                        f'... and {count - 20} more locations</div>'

            html += '</div>'
            return format_html(html)

    location_preview.short_description = 'Ã°Å¸â€”ÂºÃ¯Â¸Â Location Details'

    # ============================================================================
    # ACTIONS
    # ============================================================================

    actions = [
        'activate_items',
        'deactivate_items',
        'make_available_everywhere',
        'duplicate_item',
        'clear_all_locations',
        'increase_priority',
        'decrease_priority',
        'bulk_set_viewtype_banner',
        'bulk_set_viewtype_swiper',
        'bulk_set_viewtype_grid',
    ]

    def activate_items(self, request, queryset):
        """Activate selected items"""
        count = queryset.update(is_active=True)
        self.message_user(
            request,
            f'Ã¢Å“â€¦ Activated {count} home page item(s)',
            level=messages.SUCCESS
        )

    activate_items.short_description = "Ã¢Å“â€¦ Activate selected items"

    def deactivate_items(self, request, queryset):
        """Deactivate selected items"""
        count = queryset.update(is_active=False)
        self.message_user(
            request,
            f'Ã¢ÂÂ¸Ã¯Â¸Â Deactivated {count} home page item(s)',
            level=messages.SUCCESS
        )

    deactivate_items.short_description = "Ã¢ÂÂ¸Ã¯Â¸Â Deactivate selected items"

    def make_available_everywhere(self, request, queryset):
        """Make items available in all locations"""
        count = queryset.update(show_in_all_locations=True)
        # Clear specific locations
        for item in queryset:
            item.specific_locations.clear()
        self.message_user(
            request,
            f'Ã°Å¸Å’Â Made {count} item(s) available everywhere',
            level=messages.SUCCESS
        )

    make_available_everywhere.short_description = "Ã°Å¸Å’Â Make available everywhere"

    def clear_all_locations(self, request, queryset):
        """Clear all location settings"""
        for item in queryset:
            item.show_in_all_locations = False
            item.specific_locations.clear()
            item.save()
        self.message_user(
            request,
            f'Ã°Å¸â€”â€˜Ã¯Â¸Â Cleared locations for {queryset.count()} item(s)',
            level=messages.WARNING
        )

    clear_all_locations.short_description = "Ã°Å¸â€”â€˜Ã¯Â¸Â Clear all locations"

    def duplicate_item(self, request, queryset):
        """Duplicate selected items"""
        duplicated_count = 0
        for obj in queryset:
            # Store related items
            product_options = list(obj.product_options.all())
            service_options = list(obj.service_options.all())
            specific_locations = list(obj.specific_locations.all())

            # Duplicate the object
            obj.pk = None
            obj.title = f"{obj.title or 'Untitled'} (Copy)"
            obj.position = min(9999, obj.position + 100)
            obj.is_active = False
            obj.save()

            # Restore relationships
            obj.product_options.set(product_options)
            obj.service_options.set(service_options)
            obj.specific_locations.set(specific_locations)

            duplicated_count += 1

        self.message_user(
            request,
            f'Ã°Å¸â€œâ€¹ Duplicated {duplicated_count} item(s) (deactivated by default)',
            level=messages.SUCCESS
        )

    duplicate_item.short_description = "Ã°Å¸â€œâ€¹ Duplicate selected items"

    def increase_priority(self, request, queryset):
        """Increase position (move up in display order)"""
        for item in queryset:
            item.position = max(0, item.position - 10)
            item.save()
        self.message_user(
            request,
            f'Ã¢Â¬â€ Ã¯Â¸Â Increased priority for {queryset.count()} item(s)',
            level=messages.SUCCESS
        )

    increase_priority.short_description = "Ã¢Â¬â€ Ã¯Â¸Â Increase priority (move up)"

    def decrease_priority(self, request, queryset):
        """Decrease position (move down in display order)"""
        for item in queryset:
            item.position = min(9999, item.position + 10)
            item.save()
        self.message_user(
            request,
            f'Ã¢Â¬â€¡Ã¯Â¸Â Decreased priority for {queryset.count()} item(s)',
            level=messages.SUCCESS
        )

    decrease_priority.short_description = "Ã¢Â¬â€¡Ã¯Â¸Â Decrease priority (move down)"

    def bulk_set_viewtype_banner(self, request, queryset):
        """Set viewtype to Banner (1)"""
        count = queryset.update(viewtype=1)
        self.message_user(request, f'Ã°Å¸â€œÂ± Set {count} item(s) to Banner view')

    bulk_set_viewtype_banner.short_description = "Ã°Å¸â€œÂ± Set view: Banner"

    def bulk_set_viewtype_swiper(self, request, queryset):
        """Set viewtype to Swiper (2)"""
        count = queryset.update(viewtype=2)
        self.message_user(request, f'Ã¢â€ â€Ã¯Â¸Â Set {count} item(s) to Swiper view')

    bulk_set_viewtype_swiper.short_description = "Ã¢â€ â€Ã¯Â¸Â Set view: Swiper"

    def bulk_set_viewtype_grid(self, request, queryset):
        """Set viewtype to Grid (3)"""
        count = queryset.update(viewtype=3)
        self.message_user(request, f'Ã°Å¸â€œÅ  Set {count} item(s) to Grid view')

    bulk_set_viewtype_grid.short_description = "Ã°Å¸â€œÅ  Set view: Grid"

    # ============================================================================
    # CUSTOM METHODS
    # ============================================================================

    def save_model(self, request, obj, form, change):
        """Save with warnings instead of errors"""
        try:
            super().save_model(request, obj, form, change)

            # Show success message
            self.message_user(
                request,
                f'Ã¢Å“â€¦ Successfully saved: {obj.title or "Untitled"}',
                level=messages.SUCCESS
            )

            # Add warnings if configuration incomplete
            warnings = []

            if not obj.title:
                warnings.append("No title set")

            if obj.item_type == 'rent':
                if not obj.category:
                    warnings.append("No category selected")
                if obj.product_options.count() == 0:
                    warnings.append("No product options selected")

            if obj.item_type == 'service':
                if not obj.service_category:
                    warnings.append("No service category selected")
                if obj.service_options.count() == 0:
                    warnings.append("No service options selected")

            if not obj.show_in_all_locations and obj.specific_locations.count() == 0:
                warnings.append("No locations selected - item won't be visible")

            if warnings:
                self.message_user(
                    request,
                    f'Ã¢Å¡ Ã¯Â¸Â Warnings: {", ".join(warnings)}. You can complete this later.',
                    level=messages.WARNING
                )

        except Exception as e:
            self.message_user(
                request,
                f'Ã¢ÂÅ’ Error: {str(e)}',
                level=messages.ERROR
            )

    def changelist_view(self, request, extra_context=None):
        """Add custom context and statistics to changelist"""
        extra_context = extra_context or {}

        # Add statistics
        total_items = HomePageItem.objects.count()
        active_items = HomePageItem.objects.filter(is_active=True).count()
        rent_items = HomePageItem.objects.filter(item_type='rent').count()
        service_items = HomePageItem.objects.filter(item_type='service').count()

        extra_context['statistics'] = {
            'total_items': total_items,
            'active_items': active_items,
            'inactive_items': total_items - active_items,
            'rent_items': rent_items,
            'service_items': service_items,
        }

        # Inject custom CSS
        extra_context['custom_css'] = mark_safe("""
        <style>
        /* Home Page Item Admin Styles */
        .home-page-item-admin {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }

        .field-items_preview ul,
        .field-location_preview ul {
            max-height: 400px;
            overflow-y: auto;
            border: 1px solid #e5e7eb;
            padding: 10px;
            border-radius: 8px;
        }

        #result_list tbody tr {
            transition: all 0.2s ease;
        }

        #result_list tbody tr:hover {
            background-color: #f9fafb;
            transform: translateX(2px);
        }

        .changelist-filter {
            border-radius: 8px;
            background: #f8f9fa;
            padding: 15px;
        }

        /* Responsive adjustments */
        @media (max-width: 768px) {
            .field-items_preview ul,
            .field-location_preview ul {
                max-height: 200px;
            }
        }
        </style>
        """)

        return super().changelist_view(request, extra_context=extra_context)

    def get_queryset(self, request):
        """Optimize queryset with select_related and prefetch_related"""
        qs = super().get_queryset(request)
        return qs.select_related(
            'category',
            'service_category'
        ).prefetch_related(
            'product_options',
            'product_options__product',
            'service_options',
            'service_options__service',
            'specific_locations'
        )

    def render_change_form(self, request, context, *args, **kwargs):
        """Add custom JavaScript for dynamic field display"""
        context['admin_custom_js'] = mark_safe("""
        <script>
        (function($) {
            $(document).ready(function() {
                var itemTypeField = $('#id_item_type');
                var categoryRow = $('.form-row.field-category');
                var serviceCategoryRow = $('.form-row.field-service_category');
                var productOptionsRow = $('.form-row.field-product_options');
                var serviceOptionsRow = $('.form-row.field-service_options');

                function toggleFields() {
                    var itemType = itemTypeField.val();

                    if (itemType === 'rent') {
                        // Show rent-related fields
                        categoryRow.show();
                        productOptionsRow.show();
                        // Hide service-related fields
                        serviceCategoryRow.hide();
                        serviceOptionsRow.hide();
                    } else if (itemType === 'service') {
                        // Show service-related fields
                        serviceCategoryRow.show();
                        serviceOptionsRow.show();
                        // Hide rent-related fields
                        categoryRow.hide();
                        productOptionsRow.hide();
                    } else {
                        // Show all if type not selected
                        categoryRow.show();
                        serviceCategoryRow.show();
                        productOptionsRow.show();
                        serviceOptionsRow.show();
                    }
                }

                // Run on page load
                toggleFields();

                // Run on change
                itemTypeField.change(toggleFields);

                // Location settings toggle
                var showInAllField = $('#id_show_in_all_locations');
                var specificLocationsRow = $('.form-row.field-specific_locations');

                function toggleLocationFields() {
                    if (showInAllField.is(':checked')) {
                        specificLocationsRow.hide();
                    } else {
                        specificLocationsRow.show();
                    }
                }

                toggleLocationFields();
                showInAllField.change(toggleLocationFields);
            });
        })(django.jQuery);
        </script>
        """)

        return super().render_change_form(request, context, *args, **kwargs)

    class Media:
        css = {
            'all': ('admin/css/home_page_item.css',)
        }
        js = ('admin/js/home_page_item.js',)