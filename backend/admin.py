from django.contrib import admin
from django.contrib.admin import register
from django.contrib.auth.models import Group, User as AUser

from backend.models import User, Otp, Token, PasswordResetToken, Category, Slide, Product, ProductOption, ProductImage, \
    PageItem, Order, OrderedProduct, Notification, ContactInfo, InformMe, AppVersion, CouponUsage, Coupon, \
    BeautyService, Artist, BookingHistory, BookingRating, Booking
from backend.utils import send_user_notification

from .models import User
from .decorators import password_protected_view

admin.site.unregister(Group)
admin.site.unregister(AUser)

admin.site.site_header = "rental Cloths Admin"
admin.site.site_title = "rental-Cloths  Admin"
admin.site.index_title = "Welcome to rental Cloths  Admin Panel"


@register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['id', 'email', 'phone', 'fullname', 'address', 'pincode', 'created_at']
    fieldsets = (
        ('User info', {
            'fields': ('email', 'phone', 'fullname', 'password',)
        }),
        ('Address info', {
            'fields': ('name', 'address', 'contact_no', 'pincode', 'district', 'state',)
        }),
    )
    readonly_fields = ['password', 'email','phone','fullname','name','address','pincode','district', 'state','contact_no']
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


@register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'position', 'image']




@register(Slide)
class SlideAdmin(admin.ModelAdmin):
    list_display = ['position', 'image']





class ProductOptionInline(admin.TabularInline):
    list = ['id', 'product', 'option', 'quantity']
    model = ProductOption
    extra = 0
    show_change_link = True



@register(Product)
class ProductAdmin(admin.ModelAdmin):
    inlines = [ProductOptionInline]
    list_display = ['id', 'category', 'title', 'price', 'offer_price', 'delivery_charge', 'cod', 'created_at',
                    'updated_at']
    readonly_fields = ['star_1','star_2','star_3','star_4','star_5']
    list_filter = ['cod','category']
    search_fields = ['id','title',]
    search_help_text = "Search by Id, title"


class ProductImageInline(admin.TabularInline):
    list = ['image', 'position']
    model = ProductImage
    extra = 0
    min_num = 1


@register(ProductOption)
class ProductOptionAdmin(admin.ModelAdmin):
    inlines = [ProductImageInline]
    list_display = ['id', 'product', 'option', 'quantity']
    search_fields = ['product__title','option','quantity']
    search_help_text = 'Search by, Product, Option, Quantity'


@register(PageItem)
class PageItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'position', 'image', 'category', 'viewtype']
    filter_horizontal = ['product_options']
    list_filter = ['viewtype','category']
    search_fields = ['title']
    search_help_text = "Search by title"




@register(OrderedProduct)
class OrderedProductAdmin(admin.ModelAdmin):
    list_display = ['id', 'order', 'product_option', 'product_price', 'tx_price', 'delivery_price', 'quantity',
                    'status', 'rating', 'created_at', 'updated_at']
    readonly_fields = ['order','product_option', 'product_price', 'tx_price', 'delivery_price', 'quantity', 'rating']
    search_fields = ['id']
    search_help_text = "Search by Id"
    list_filter = ['status']
    ordering = ['-created_at']

    def save_model(self, request, ordered_product, form, change):
        super(OrderedProductAdmin, self).save_model( request, ordered_product, form, change)
        user = ordered_product.order.user
        title = "ORDER "+ordered_product.status
        body = "Your "+ordered_product.product_option.__str__()+" has been "+ordered_product.status+"."
        image = ordered_product.product_option.images_set.first().image
        print("ORDER STATUS: "+title)
        send_user_notification(user,title,body,image)

    def has_add_permission(self, request):
        return False



class OrderedProductInline(admin.TabularInline):
    model = OrderedProduct
    list = ['id', 'product_option', 'product_price', 'tx_price', 'delivery_price', 'quantity', 'status']
    readonly_fields = ['product_option', 'product_price', 'tx_price', 'delivery_price', 'quantity', 'status', 'rating']

    show_change_link = True

    extra = 0

    def has_add_permission(self, request, obj):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@register(Order)
class OrderAdmin(admin.ModelAdmin):
    inlines = [OrderedProductInline]
    list_display = ['id','seen', 'user', 'tx_amount', 'payment_mode', 'address', 'tx_id', 'tx_status', 'tx_time', 'tx_msg',
                    'from_cart', 'created_at', 'updated_at']
    list_filter = ['payment_mode', 'tx_status', 'from_cart']
    ordering = ['-created_at']
    readonly_fields = ['user', 'tx_amount', 'payment_mode', 'tx_id', 'tx_time', 'tx_msg', 'from_cart']
    search_fields = ['id','user__email','address','tx_id', ]
    search_help_text = "Search by Id, user, address, tx_id"

    def has_add_permission(self, request):
        return False


@register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'body', 'image', 'seen', 'created_at']



@admin.register(ContactInfo)
class ContactInfoAdmin(admin.ModelAdmin):
    list_display = ['id', 'phone_number']
    fields = ['phone_number']
    search_fields = ['phone_number']
    search_help_text = "Search by phone number"

    def has_add_permission(self, request):
        # Allow only one ContactInfo entry
        if ContactInfo.objects.exists():
            return False
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

    def save_model(self, request, obj, form, change):
        # Deactivate other versions for the same platform when a new active version is saved
        if obj.is_active:
            AppVersion.objects.filter(
                platform=obj.platform,
                is_active=True
            ).exclude(pk=obj.pk).update(is_active=False)
        super().save_model(request, obj, form, change)

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


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = [
        'code', 'name', 'discount_type', 'discount_value',
        'min_order_amount', 'used_count', 'usage_limit',
        'is_active', 'valid_from', 'valid_until'
    ]
    list_filter = ['discount_type', 'is_active', 'valid_from', 'valid_until']
    search_fields = ['code', 'name', 'description']
    readonly_fields = ['used_count', 'created_at', 'updated_at']

    fieldsets = (
        ('Basic Information', {
            'fields': ('code', 'name', 'description', 'is_active')
        }),
        ('Discount Settings', {
            'fields': ('discount_type', 'discount_value', 'max_discount', 'min_order_amount')
        }),
        ('Usage Limits', {
            'fields': ('usage_limit', 'usage_limit_per_user', 'used_count')
        }),
        ('Validity Period', {
            'fields': ('valid_from', 'valid_until')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(CouponUsage)
class CouponUsageAdmin(admin.ModelAdmin):
    list_display = ['coupon', 'user', 'discount_amount', 'used_at']
    list_filter = ['coupon', 'used_at']
    search_fields = ['coupon__code', 'user__email']
    readonly_fields = ['used_at']


@admin.register(BeautyService)
class BeautyServiceAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'base_price', 'duration_minutes', 'icon', 'is_active', 'created_at']
    list_filter = ['category', 'is_active', 'created_at']
    search_fields = ['name', 'description', 'category']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Service Information', {
            'fields': ('name', 'description', 'category', 'icon')
        }),
        ('Pricing & Duration', {
            'fields': ('base_price', 'duration_minutes')
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    actions = ['make_active', 'make_inactive']

    def make_active(self, request, queryset):
        queryset.update(is_active=True)
        self.message_user(request, f"{queryset.count()} service(s) activated.")

    make_active.short_description = "Activate selected services"

    def make_inactive(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, f"{queryset.count()} service(s) deactivated.")

    make_inactive.short_description = "Deactivate selected services"


@admin.register(Artist)
class ArtistAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'experience_years', 'average_rating', 'total_bookings', 'is_available',
                    'created_at']
    list_filter = ['is_available', 'experience_years', 'created_at', 'specializations']
    search_fields = ['name', 'phone', 'email', 'location']
    readonly_fields = ['total_bookings', 'average_rating', 'total_reviews', 'created_at', 'updated_at']
    filter_horizontal = ['specializations']

    fieldsets = (
        ('Personal Information', {
            'fields': ('name', 'phone', 'email', 'bio', 'profile_image')
        }),
        ('Professional Details', {
            'fields': ('experience_years', 'specializations', 'location')
        }),
        ('Statistics', {
            'fields': ('total_bookings', 'average_rating', 'total_reviews'),
            'classes': ('collapse',)
        }),
        ('Availability', {
            'fields': ('is_available',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('specializations')


class BookingHistoryInline(admin.TabularInline):
    model = BookingHistory
    extra = 0
    readonly_fields = ['action', 'description', 'performed_by', 'created_at']
    can_delete = False

    def has_add_permission(self, request, obj):
        return False


class BookingRatingInline(admin.StackedInline):
    model = BookingRating
    extra = 0
    readonly_fields = ['overall_rating', 'service_quality', 'punctuality', 'professionalism', 'review_text',
                       'created_at']
    can_delete = False

    def has_add_permission(self, request, obj):
        return False


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = [
        'booking_number', 'user_email', 'service_name', 'artist_name',
        'scheduled_datetime', 'status', 'payment_status', 'total_amount', 'created_at'
    ]
    list_filter = ['status', 'payment_status', 'scheduled_date', 'created_at', 'service__category']
    search_fields = ['booking_number', 'user__email', 'user__fullname', 'service__name', 'artist__name']
    readonly_fields = ['booking_number', 'created_at', 'updated_at', 'cancelled_at', 'completed_at']
    date_hierarchy = 'scheduled_date'
    ordering = ['-created_at']

    inlines = [BookingRatingInline, BookingHistoryInline]

    fieldsets = (
        ('Booking Information', {
            'fields': ('booking_number', 'user', 'service', 'artist')
        }),
        ('Schedule', {
            'fields': ('scheduled_date', 'scheduled_time', 'duration_minutes')
        }),
        ('Pricing', {
            'fields': ('service_price', 'additional_charges', 'discount', 'total_amount')
        }),
        ('Status & Payment', {
            'fields': ('status', 'payment_status', 'payment_method', 'transaction_id')
        }),
        ('Location', {
            'fields': ('service_address', 'latitude', 'longitude'),
            'classes': ('collapse',)
        }),
        ('Notes', {
            'fields': ('customer_notes', 'artist_notes'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'cancelled_at', 'completed_at'),
            'classes': ('collapse',)
        }),
    )

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = 'User Email'
    user_email.admin_order_field = 'user__email'

    def service_name(self, obj):
        return obj.service.name

    service_name.short_description = 'Service'
    service_name.admin_order_field = 'service__name'

    def artist_name(self, obj):
        return obj.artist.name

    artist_name.short_description = 'Artist'
    artist_name.admin_order_field = 'artist__name'

    def scheduled_datetime(self, obj):
        return f"{obj.scheduled_date} {obj.scheduled_time}"

    scheduled_datetime.short_description = 'Scheduled Date & Time'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user', 'service', 'artist')

    # Custom actions
    actions = ['mark_as_confirmed', 'mark_as_completed', 'mark_as_cancelled']

    def mark_as_confirmed(self, request, queryset):
        count = 0
        for booking in queryset:
            if booking.status == 'PENDING':
                booking.status = 'CONFIRMED'
                booking.save()

                # Create history entry
                BookingHistory.objects.create(
                    booking=booking,
                    action='CONFIRMED',
                    description='Booking confirmed by admin',
                    performed_by=request.user if hasattr(request.user, 'id') else None
                )
                count += 1

        self.message_user(request, f"{count} booking(s) marked as confirmed.")

    mark_as_confirmed.short_description = "Mark selected bookings as confirmed"

    def mark_as_completed(self, request, queryset):
        from django.utils import timezone
        count = 0
        for booking in queryset:
            if booking.status in ['CONFIRMED', 'IN_PROGRESS']:
                booking.status = 'COMPLETED'
                booking.completed_at = timezone.now()
                booking.save()

                # Create history entry
                BookingHistory.objects.create(
                    booking=booking,
                    action='COMPLETED',
                    description='Booking completed by admin',
                    performed_by=request.user if hasattr(request.user, 'id') else None
                )
                count += 1

        self.message_user(request, f"{count} booking(s) marked as completed.")

    mark_as_completed.short_description = "Mark selected bookings as completed"

    def mark_as_cancelled(self, request, queryset):
        from django.utils import timezone
        count = 0
        for booking in queryset:
            if booking.status in ['PENDING', 'CONFIRMED']:
                booking.status = 'CANCELLED'
                booking.cancelled_at = timezone.now()
                booking.save()

                # Create history entry
                BookingHistory.objects.create(
                    booking=booking,
                    action='CANCELLED',
                    description='Booking cancelled by admin',
                    performed_by=request.user if hasattr(request.user, 'id') else None
                )
                count += 1

        self.message_user(request, f"{count} booking(s) marked as cancelled.")

    mark_as_cancelled.short_description = "Mark selected bookings as cancelled"


@admin.register(BookingRating)
class BookingRatingAdmin(admin.ModelAdmin):
    list_display = ['booking_number', 'overall_rating', 'service_quality', 'punctuality', 'professionalism',
                    'created_at']
    list_filter = ['overall_rating', 'service_quality', 'punctuality', 'professionalism', 'created_at']
    search_fields = ['booking__booking_number', 'booking__user__email', 'review_text']
    readonly_fields = ['booking', 'created_at', 'updated_at']

    def booking_number(self, obj):
        return obj.booking.booking_number

    booking_number.short_description = 'Booking Number'
    booking_number.admin_order_field = 'booking__booking_number'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('booking__user', 'booking')


@admin.register(BookingHistory)
class BookingHistoryAdmin(admin.ModelAdmin):
    list_display = ['booking_number', 'action', 'description', 'performed_by', 'created_at']
    list_filter = ['action', 'created_at']
    search_fields = ['booking__booking_number', 'description']
    readonly_fields = ['booking', 'action', 'description', 'performed_by', 'created_at']
    date_hierarchy = 'created_at'

    def booking_number(self, obj):
        return obj.booking.booking_number

    booking_number.short_description = 'Booking Number'
    booking_number.admin_order_field = 'booking__booking_number'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('booking', 'performed_by')


# Custom admin views for reports and analytics
class BookingReportAdmin:
    """
    Custom admin view for booking reports and analytics
    """

    def get_booking_stats(self):
        from django.db.models import Count, Sum, Avg
        from django.utils import timezone
        from datetime import timedelta

        now = timezone.now()
        last_30_days = now - timedelta(days=30)

        stats = {
            'total_bookings': Booking.objects.count(),
            'bookings_last_30_days': Booking.objects.filter(created_at__gte=last_30_days).count(),
            'completed_bookings': Booking.objects.filter(status='COMPLETED').count(),
            'cancelled_bookings': Booking.objects.filter(status='CANCELLED').count(),
            'total_revenue': Booking.objects.filter(status='COMPLETED').aggregate(
                total=Sum('total_amount')
            )['total'] or 0,
            'avg_booking_value': Booking.objects.filter(status='COMPLETED').aggregate(
                avg=Avg('total_amount')
            )['avg'] or 0,
        }

        # Top services
        top_services = BeautyService.objects.annotate(
            booking_count=Count('booking')
        ).order_by('-booking_count')[:5]

        stats['top_services'] = [
            {'name': service.name, 'count': service.booking_count}
            for service in top_services
        ]

        # Top artists
        top_artists = Artist.objects.annotate(
            booking_count=Count('bookings')
        ).order_by('-booking_count')[:5]

        stats['top_artists'] = [
            {'name': artist.name, 'count': artist.booking_count, 'rating': artist.average_rating}
            for artist in top_artists
        ]

        return stats