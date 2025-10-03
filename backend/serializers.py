from datetime import datetime, timezone, timedelta

from django.core.validators import MinValueValidator, MaxValueValidator
from rest_framework.fields import SerializerMethodField
from rest_framework.serializers import ModelSerializer
from rest_framework import serializers

from backend.models import User, Category, Slide, Product, ProductOption, ProductImage, PageItem, OrderedProduct, \
    Notification, ContactInfo, InformMe, AppVersion, Artist, BookingRating, BookingHistory, \
    Booking, BeautyService


class UserSerializer(ModelSerializer):
    notifications = SerializerMethodField()
    class Meta:
        model = User
        fields = ['email','notifications', 'phone', 'fullname', 'wishlist', 'cart', 'name', 'address', 'contact_no', 'pincode', 'state',
                  'district']

    def get_notifications(self,obj):
        list = obj.notifications_set.filter(seen=False)
        return len(list)


class AddressSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = ['name', 'address', 'contact_no', 'pincode', 'state', 'district']


class CategorySerializer(ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'position', 'image']

    def get_image(self, obj):
        request = self.context.get('request')
        if obj.image:
            image_url = obj.image.url
            return request.build_absolute_uri(image_url) if request else image_url
        return None


class SlideSerializer(ModelSerializer):
    class Meta:
        model = Slide
        fields = ['position', 'image']


# Enhanced ProductSerializer with better image handling
class ProductSerializer(ModelSerializer):
    options = SerializerMethodField()
    category_name = SerializerMethodField()

    class Meta:
        model = Product
        fields = ['id', 'title', 'description', 'price', 'offer_price', 'delivery_charge', 'cod', 'star_5', 'star_4',
                  'star_3', 'star_2', 'star_1', 'options', 'category_name', 'created_at', 'updated_at']

    def get_options(self, obj):
        options = obj.options_set.all()
        data = ProductOptionSerializer(options, many=True, context=self.context).data
        return data

    def get_category_name(self, obj):
        return obj.category.name if obj.category else None




class ProductOptionSerializer(ModelSerializer):
    images = SerializerMethodField()

    class Meta:
        model = ProductOption
        fields = ['id', 'option', 'quantity', 'images']

    def get_images(self, obj):
        images = obj.images_set.all()
        data = ProductImageSerializer(images, many=True).data
        return data




class ProductImageSerializer(ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ['position', 'image', 'product_option']


class WishlistSerializer(ModelSerializer):
    id = SerializerMethodField()
    title = SerializerMethodField()
    price = SerializerMethodField()
    offer_price = SerializerMethodField()
    image = SerializerMethodField()

    def get_id(self, obj):
        return obj.product.id

    def get_title(self, obj):
        return obj.__str__()

    def get_price(self, obj):
        return obj.product.price

    def get_offer_price(self, obj):
        return obj.product.offer_price

    def get_image(self, obj):
        return ProductImageSerializer(obj.images_set.order_by('position').first(), many=False).data.get(
            'image')

    class Meta:
        model = ProductOption
        fields = ['id', 'title', 'image', 'price', 'offer_price']


class CartSerializer(WishlistSerializer):
    cod = SerializerMethodField()
    delivery_charge = SerializerMethodField()
    id = SerializerMethodField()

    def get_cod(self, obj):
        return obj.product.cod

    def get_delivery_charge(self, obj):
        return obj.product.delivery_charge

    def get_id(self, obj):
        return obj.id

    class Meta:
        model = ProductOption
        fields = ['id', 'title', 'image', 'price', 'offer_price', 'quantity', 'cod', 'delivery_charge']


class PageItemSerializer(ModelSerializer):
    product_options = SerializerMethodField()

    class Meta:
        model = PageItem
        fields = ['id', 'position', 'image', 'category', 'title', 'viewtype', 'product_options']

    def get_product_options(self, obj):
        options = obj.product_options.all()[:8]
        data = []
        for option in options:
            data.append({
                'id': option.product.id,
                'image': ProductImageSerializer(option.images_set.order_by('position').first(), many=False).data.get(
                    'image'),
                'title': option.__str__(),
                'price': option.product.price,
                'offer_price': option.product.offer_price,
            })

        return data


class OrderItemSerializer(ModelSerializer):
    title = SerializerMethodField()
    image = SerializerMethodField()
    created_at = SerializerMethodField()

    class Meta:
        model = OrderedProduct
        fields = ['id', 'title', 'image', 'created_at', 'quantity', 'status', 'rating']

    def get_title(self, obj):
        return obj.product_option.__str__()

    def get_image(self, obj):
        return ProductImageSerializer(obj.product_option.images_set.order_by('position').first(), many=False).data.get(
            'image')

    def get_created_at(self, obj):
        return obj.created_at.strftime("%d %b %Y %H:%M %p")


class OrderDetailsSerializer(OrderItemSerializer):
    payment_mode = SerializerMethodField()
    address = SerializerMethodField()
    tx_id = SerializerMethodField()
    tx_status = SerializerMethodField()
    user_phone = SerializerMethodField()

    #temp. code
    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6,
        required=False, allow_null=True
    )
    longitude = serializers.DecimalField(
        max_digits=9, decimal_places=6,
        required=False, allow_null=True
    )

    class Meta:
        model = OrderedProduct
        fields = ['id', 'title', 'image', 'created_at', 'quantity', 'status', 'rating'
                  ,'product_price','tx_price','delivery_price','payment_mode','address','tx_id','tx_status','latitude','longitude','user_phone']

    def get_payment_mode(self,obj):
        return obj.order.payment_mode

    def get_address(self,obj):
        return obj.order.address

    def get_tx_id(self,obj):
        return obj.order.tx_id

    def get_tx_status(self,obj):
        return obj.order.tx_status

    def get_user_phone(self, obj):
        return obj.order.user.phone


class NotificationSerializer(ModelSerializer):
    created_at = SerializerMethodField()
    class Meta:
        model = Notification
        fields = ['id','title','body','image','seen','created_at']


    def get_created_at(self, obj):
        return obj.created_at.strftime("%d %b %Y %H:%M %p")

class PrivacyPolicySerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False)

class ContactUsSerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False)


# class OrderDetailSerializer(serializers.ModelSerializer):
#     user = serializers.CharField(source='user.email', read_only=True)  # Fetch email from user

#     class Meta:
#         model = Order
#         fields = [
#             'id', 'user', 'tx_amount', 'payment_mode', 'address', 'tx_id',
#             'tx_status', 'tx_time', 'tx_msg', 'from_cart', 'created_at', 'updated_at'
#         ]




class ItemOrderSerializer(serializers.ModelSerializer):
    product_option = serializers.CharField(source='product_option.option')  # Option name
    product_title = serializers.CharField(source='product_option.product.title')  # Product title
    product_image = serializers.SerializerMethodField()  # Absolute product image URL
    user_fullname = serializers.CharField(source='order.user.fullname', read_only=True)  # User full name
    user_phone = serializers.CharField(source='order.user.phone', read_only=True)  # User phone number
    payment_mode = serializers.CharField(source='order.payment_mode', read_only=True)  # Payment mode
    tx_status = serializers.CharField(source='order.tx_status', read_only=True)  # Transaction status
    tx_msg = serializers.CharField(source='order.tx_msg', read_only=True)  # Transaction message
    address = serializers.CharField(source='order.address', read_only=True)  # Order address
    related_products = serializers.SerializerMethodField()  # Add related products


    class Meta:
        model = OrderedProduct
        fields = [
            'id',
            'quantity',
            'product_option',
            'product_title',
            'product_image',
            'user_fullname',
            'user_phone',
            'payment_mode',
            'tx_status',
            'tx_msg',
            'address',
            'tx_price',
            'product_price',
            'delivery_price',
            'status',
            'created_at',
            'updated_at',
            'related_products',
        ]

    def get_product_image(self, obj):
        if obj.product_option.images_set.exists():
            request = self.context.get('request')  # Access the request object
            image_url = obj.product_option.images_set.first().image.url
            return request.build_absolute_uri(image_url) if request else f"/{image_url.lstrip('/')}"
        return None

    def get_related_products(self, obj):
        # Get all products from the same order, excluding the current one
        related_products = OrderedProduct.objects.filter(
            order=obj.order
        ).exclude(
            id=obj.id
        )

        # Return serialized data for related products
        return RelatedProductSerializer(related_products, many=True, context=self.context).data

class ContactInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactInfo
        fields = ['phone_number']


class RelatedProductSerializer(serializers.ModelSerializer):
    product_option = serializers.CharField(source='product_option.option')
    product_title = serializers.CharField(source='product_option.product.title')
    product_image = serializers.SerializerMethodField()

    class Meta:
        model = OrderedProduct
        fields = [
            'id',
            'product_option',
            'product_title',
            'product_image',
            'quantity',
            'product_price',
            'tx_price',
            'status',
        ]

    def get_product_image(self, obj):
        if obj.product_option.images_set.exists():
            request = self.context.get('request')
            image_url = obj.product_option.images_set.first().image.url
            return request.build_absolute_uri(image_url) if request else f"/{image_url.lstrip('/')}"
        return None


# Validation serializers for API requests
class ProductCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=500)
    description = serializers.CharField(max_length=100000, allow_blank=True)
    price = serializers.IntegerField(min_value=0)
    offer_price = serializers.IntegerField(min_value=0)
    delivery_charge = serializers.IntegerField(min_value=0, default=0)
    cod = serializers.BooleanField(default=False)
    category = serializers.CharField()

    def validate_category(self, value):
        try:
            category = Category.objects.get(id=value)
            return category
        except Category.DoesNotExist:
            raise serializers.ValidationError("Category not found")

    def validate(self, data):
        if data['offer_price'] > data['price']:
            raise serializers.ValidationError("Offer price cannot be greater than original price")
        return data


class ProductOptionCreateSerializer(serializers.Serializer):
    product = serializers.CharField()
    option = serializers.CharField(max_length=50)
    quantity = serializers.IntegerField(min_value=0, default=0)

    def validate_product(self, value):
        try:
            product = Product.objects.get(id=value)
            return product
        except Product.DoesNotExist:
            raise serializers.ValidationError("Product not found")


class ProductUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=500, required=False)
    description = serializers.CharField(max_length=100000, allow_blank=True, required=False)
    price = serializers.IntegerField(min_value=0, required=False)
    offer_price = serializers.IntegerField(min_value=0, required=False)
    delivery_charge = serializers.IntegerField(min_value=0, required=False)
    cod = serializers.BooleanField(required=False)

    def validate(self, data):
        # If both price and offer_price are provided, validate them
        if 'price' in data and 'offer_price' in data:
            if data['offer_price'] > data['price']:
                raise serializers.ValidationError("Offer price cannot be greater than original price")
        return data


class ProductOptionUpdateSerializer(serializers.Serializer):
    option = serializers.CharField(max_length=50, required=False)
    quantity = serializers.IntegerField(min_value=0, required=False)


class BulkStockUpdateSerializer(serializers.Serializer):
    option_id = serializers.CharField()
    quantity = serializers.IntegerField(min_value=0)

    def validate_option_id(self, value):
        try:
            option = ProductOption.objects.get(id=value)
            return option
        except ProductOption.DoesNotExist:
            raise serializers.ValidationError("Product option not found")


class ImageUploadSerializer(serializers.Serializer):
    product_option = serializers.CharField()
    position = serializers.IntegerField(min_value=0, default=0)
    image = serializers.ImageField()

    def validate_product_option(self, value):
        try:
            option = ProductOption.objects.get(id=value)
            return option
        except ProductOption.DoesNotExist:
            raise serializers.ValidationError("Product option not found")

    def validate_image(self, value):
        # Validate file size (5MB max)
        if value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Image file too large (max 5MB)")

class InformMeSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    product_option = serializers.PrimaryKeyRelatedField(
        queryset=ProductOption.objects.all(),
        allow_null=True,
        required=False
    )

    class Meta:
        model = InformMe
        fields = [
            'id', 'user', 'product', 'product_option',
            'price', 'offer_price', 'created_at'
        ]
        read_only_fields = ['id', 'user', 'price', 'created_at']

class VersionCheckRequestSerializer(serializers.Serializer):
    platform = serializers.ChoiceField(choices=['android', 'ios'])
    current_version = serializers.CharField(max_length=20)
    current_build = serializers.IntegerField()
    device_id = serializers.CharField(max_length=255, required=False, allow_blank=True)

class VersionCheckResponseSerializer(serializers.Serializer):
    has_update = serializers.BooleanField()
    is_force_update = serializers.BooleanField()
    current_version = serializers.CharField()
    latest_version = serializers.CharField()
    latest_build = serializers.IntegerField()
    update_message = serializers.CharField()
    release_notes = serializers.CharField()
    store_url = serializers.URLField()
    min_supported_version = serializers.CharField()

class AppVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppVersion
        fields = '__all__'


class BeautyServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = BeautyService
        fields = [
            'id', 'name', 'description', 'category', 'base_price',
            'duration_minutes', 'icon', 'is_active'
        ]


class ArtistSerializer(serializers.ModelSerializer):
    specializations = BeautyServiceSerializer(many=True, read_only=True)
    profile_image_url = serializers.SerializerMethodField()

    class Meta:
        model = Artist
        fields = [
            'id', 'name', 'phone', 'email', 'bio', 'profile_image_url',
            'experience_years', 'specializations', 'total_bookings',
            'average_rating', 'total_reviews', 'is_available', 'location'
        ]

    def get_profile_image_url(self, obj):
        request = self.context.get('request')
        if obj.profile_image:
            return request.build_absolute_uri(obj.profile_image.url) if request else obj.profile_image.url
        return None


class BookingListSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source='service.name', read_only=True)
    service_icon = serializers.CharField(source='service.icon', read_only=True)
    artist_name = serializers.CharField(source='artist.name', read_only=True)
    booking_date = serializers.DateField(source='scheduled_date', read_only=True)
    booking_time = serializers.TimeField(source='scheduled_time', read_only=True, format='%I:%M %p')
    can_cancel = serializers.BooleanField(read_only=True)
    can_reschedule = serializers.BooleanField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    # Rating info for completed bookings
    has_rating = serializers.SerializerMethodField()
    user_rating = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = [
            'id', 'booking_number', 'service_name', 'service_icon', 'artist_name',
            'booking_date', 'booking_time', 'status', 'total_amount',
            'can_cancel', 'can_reschedule', 'is_active', 'created_at',
            'has_rating', 'user_rating'
        ]

    def get_has_rating(self, obj):
        return hasattr(obj, 'rating')

    def get_user_rating(self, obj):
        if hasattr(obj, 'rating'):
            return {
                'overall_rating': obj.rating.overall_rating,
                'review_text': obj.rating.review_text
            }
        return None


class BookingDetailSerializer(serializers.ModelSerializer):
    service = BeautyServiceSerializer(read_only=True)
    artist = ArtistSerializer(read_only=True)
    booking_date = serializers.DateField(source='scheduled_date', read_only=True)
    booking_time = serializers.TimeField(source='scheduled_time', read_only=True, format='%I:%M %p')
    can_cancel = serializers.BooleanField(read_only=True)
    can_reschedule = serializers.BooleanField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    # Rating info
    rating_details = serializers.SerializerMethodField()

    # History
    status_history = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = [
            'id', 'booking_number', 'service', 'artist', 'booking_date', 'booking_time',
            'duration_minutes', 'service_price', 'additional_charges', 'discount',
            'total_amount', 'status', 'payment_status', 'payment_method',
            'service_address', 'customer_notes', 'artist_notes',
            'can_cancel', 'can_reschedule', 'is_active', 'created_at',
            'rating_details', 'status_history'
        ]

    def get_rating_details(self, obj):
        if hasattr(obj, 'rating'):
            return {
                'overall_rating': obj.rating.overall_rating,
                'service_quality': obj.rating.service_quality,
                'punctuality': obj.rating.punctuality,
                'professionalism': obj.rating.professionalism,
                'review_text': obj.rating.review_text,
                'created_at': obj.rating.created_at
            }
        return None

    def get_status_history(self, obj):
        history = obj.history.all()[:5]  # Last 5 status changes
        return [
            {
                'action': h.action,
                'description': h.description,
                'created_at': h.created_at
            }
            for h in history
        ]


class BookingCreateSerializer(serializers.ModelSerializer):
    service_id = serializers.UUIDField(write_only=True)
    artist_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = Booking
        fields = [
            'service_id', 'artist_id', 'scheduled_date', 'scheduled_time',
            'service_address', 'latitude', 'longitude', 'customer_notes'
        ]

    def validate(self, data):
        from django.utils import timezone
        from datetime import datetime, timedelta

        # Validate service exists
        try:
            service = BeautyService.objects.get(id=data['service_id'], is_active=True)
        except BeautyService.DoesNotExist:
            raise serializers.ValidationError("Service not found or not available")

        # Validate artist exists and is available
        try:
            artist = Artist.objects.get(id=data['artist_id'], is_available=True)
        except Artist.DoesNotExist:
            raise serializers.ValidationError("Artist not found or not available")

        # Validate date is in future
        booking_datetime = datetime.combine(data['scheduled_date'], data['scheduled_time'])
        if booking_datetime <= timezone.now() + timedelta(hours=2):
            raise serializers.ValidationError("Booking must be at least 2 hours in advance")

        # Check artist availability for that time slot
        existing_booking = Booking.objects.filter(
            artist=artist,
            scheduled_date=data['scheduled_date'],
            scheduled_time__range=[
                (datetime.combine(data['scheduled_date'], data['scheduled_time']) -
                 timedelta(minutes=service.duration_minutes)).time(),
                (datetime.combine(data['scheduled_date'], data['scheduled_time']) +
                 timedelta(minutes=service.duration_minutes)).time()
            ],
            status__in=['PENDING', 'CONFIRMED', 'IN_PROGRESS']
        ).exists()

        if existing_booking:
            raise serializers.ValidationError("Artist is not available at this time")

        data['service'] = service
        data['artist'] = artist
        return data

    def create(self, validated_data):
        service = validated_data.pop('service')
        artist = validated_data.pop('artist')
        validated_data.pop('service_id')
        validated_data.pop('artist_id')

        user = self.context['request'].user

        booking = Booking.objects.create(
            user=user,
            service=service,
            artist=artist,
            duration_minutes=service.duration_minutes,
            service_price=service.base_price,
            total_amount=service.base_price,
            **validated_data
        )

        # Create history entry
        BookingHistory.objects.create(
            booking=booking,
            action='CREATED',
            description='Booking created successfully',
            performed_by=user
        )

        return booking


class BookingUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = ['status', 'artist_notes']

    def validate_status(self, value):
        booking = self.instance
        valid_transitions = {
            'PENDING': ['CONFIRMED', 'CANCELLED'],
            'CONFIRMED': ['IN_PROGRESS', 'CANCELLED', 'RESCHEDULED'],
            'IN_PROGRESS': ['COMPLETED', 'CANCELLED'],
            'COMPLETED': [],  # No transitions from completed
            'CANCELLED': [],  # No transitions from cancelled
            'RESCHEDULED': ['CONFIRMED', 'CANCELLED'],
        }

        current_status = booking.status
        if value not in valid_transitions.get(current_status, []):
            raise serializers.ValidationError(
                f"Cannot change status from {current_status} to {value}"
            )
        return value

    def update(self, instance, validated_data):
        old_status = instance.status
        instance = super().update(instance, validated_data)

        # Create history entry if status changed
        if 'status' in validated_data and old_status != validated_data['status']:
            BookingHistory.objects.create(
                booking=instance,
                action=validated_data['status'],
                description=f"Status changed from {old_status} to {validated_data['status']}",
                performed_by=self.context['request'].user
            )

        return instance


class RescheduleBookingSerializer(serializers.Serializer):
    new_date = serializers.DateField()
    new_time = serializers.TimeField()
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate(self, data):
        from django.utils import timezone
        from datetime import datetime, timedelta

        booking = self.context['booking']

        # Check if booking can be rescheduled
        if not booking.can_reschedule:
            raise serializers.ValidationError("This booking cannot be rescheduled")

        # Validate new date is in future
        booking_datetime = datetime.combine(data['new_date'], data['new_time'])
        if booking_datetime <= timezone.now() + timedelta(hours=4):
            raise serializers.ValidationError("New booking time must be at least 4 hours in advance")

        # Check artist availability for new time slot
        existing_booking = Booking.objects.filter(
            artist=booking.artist,
            scheduled_date=data['new_date'],
            scheduled_time__range=[
                (booking_datetime - timedelta(minutes=booking.duration_minutes)).time(),
                (booking_datetime + timedelta(minutes=booking.duration_minutes)).time()
            ],
            status__in=['PENDING', 'CONFIRMED', 'IN_PROGRESS']
        ).exclude(id=booking.id).exists()

        if existing_booking:
            raise serializers.ValidationError("Artist is not available at the new requested time")

        return data


class BookingRatingSerializer(serializers.ModelSerializer):
    booking_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = BookingRating
        fields = [
            'booking_id', 'overall_rating', 'service_quality', 'punctuality',
            'professionalism', 'review_text', 'is_anonymous'
        ]

    def validate_booking_id(self, value):
        user = self.context['request'].user
        try:
            booking = Booking.objects.get(
                id=value,
                user=user,
                status='COMPLETED'
            )
            if hasattr(booking, 'rating'):
                raise serializers.ValidationError("This booking has already been rated")
            return booking
        except Booking.DoesNotExist:
            raise serializers.ValidationError("Booking not found or not completed")

    def create(self, validated_data):
        booking = validated_data.pop('booking_id')
        rating = BookingRating.objects.create(booking=booking, **validated_data)

        # Create history entry
        BookingHistory.objects.create(
            booking=booking,
            action='RATED',
            description=f"Service rated {rating.overall_rating} stars",
            performed_by=self.context['request'].user
        )

        return rating


class CancelBookingSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)
    refund_requested = serializers.BooleanField(default=True)

    def validate(self, data):
        booking = self.context['booking']
        if not booking.can_cancel:
            raise serializers.ValidationError("This booking cannot be cancelled")
        return data