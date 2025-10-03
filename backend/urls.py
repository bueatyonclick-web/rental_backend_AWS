from django.urls import path

from backend.views import (
    # Existing authentication endpoints
    request_otp, verify_otp, create_account, login, userdata, resend_otp, logout,

    # Existing home and page items endpoints
    home_screen_data, get_page_items_by_category, get_categories_with_page_items,

    # Existing product endpoints
    search_products, category_products, all_categories, page_item_products,

    # Existing cart and wishlist endpoints
    add_to_cart, remove_from_cart, get_cart_items,
    add_to_wishlist, remove_from_wishlist, get_wishlist_items, product_details,
    validate_cart, move_to_wishlist, clear_cart, cart_summary, bulk_update_cart,
    apply_coupon, get_cart_items_enhanced, get_order_tracking, get_user_orders,
    update_order_rating, cancel_order, slides, get_profile, update_profile,
    upload_profile_image, edit_profile, get_addresses, add_address, update_address,
    delete_address, get_wishlist_items_enhanced, add_to_wishlist_enhanced,
    remove_from_wishlist_enhanced, move_wishlist_to_cart, share_wishlist,
    undo_wishlist_removal, clear_wishlist,

    # NEW: Booking management endpoints
    get_user_bookings, get_booking_detail, create_booking, cancel_booking,
    reschedule_booking, rate_booking, contact_artist, book_again,
    get_available_services, get_available_artists, get_artist_availability,
    get_booking_stats, get_payment_history, get_payment_summary, get_payment_detail, download_receipt, request_refund,
)

urlpatterns = [
    # =============================================================================
    # AUTHENTICATION ENDPOINTS
    # =============================================================================

    # OTP and Account Management
    path('request_otp/', request_otp, name='request_otp'),
    path('resend_otp/', resend_otp, name='resend_otp'),
    path('verify_otp/', verify_otp, name='verify_otp'),
    path('create_account/', create_account, name='create_account'),

    # Login and Session Management
    path('login/', login, name='login'),
    path('logout/', logout, name='logout'),
    path('userdata/', userdata, name='userdata'),

    # =============================================================================
    # HOME SCREEN AND NAVIGATION ENDPOINTS
    # =============================================================================

    # Main home screen data (user info, categories, slides, page items, products)
    path('home/', home_screen_data, name='home_screen_data'),

    # Page items management
    path('category/<int:category_id>/page-items/', get_page_items_by_category, name='category_page_items'),
    path('categories/with-page-items/', get_categories_with_page_items, name='categories_with_page_items'),

    # Promotional slides
    path('slides/', slides, name='slides'),

    # =============================================================================
    # VIEW ALL ENDPOINTS - PRODUCT SEARCH AND FILTERING
    # =============================================================================

    # Advanced product search with filtering and sorting
    path('search/', search_products, name='search_products'),

    # Category-specific products with filtering and sorting
    path('category/<int:category_id>/products/', category_products, name='category_products'),

    # All categories view
    path('categories/', all_categories, name='all_categories'),

    # Page item products with filtering and sorting
    path('page-item-products/', page_item_products, name='page_item_products'),

    # =============================================================================
    # PRODUCT DETAILS ENDPOINTS
    # =============================================================================

    # Enhanced product details with ratings, reviews, and related products
    path('product/<uuid:product_id>/', product_details, name='product_details'),

    # =============================================================================
    # CART MANAGEMENT ENDPOINTS
    # =============================================================================

    # Basic cart operations
    path('cart/add/', add_to_cart, name='add_to_cart'),
    path('cart/remove/<uuid:product_option_id>/', remove_from_cart, name='remove_from_cart'),
    path('cart/', get_cart_items, name='get_cart_items'),

    # Enhanced cart operations
    path('cart/enhanced/', get_cart_items_enhanced, name='get_cart_items_enhanced'),
    path('cart/apply-coupon/', apply_coupon, name='apply_coupon'),
    path('cart/bulk-update/', bulk_update_cart, name='bulk_update_cart'),
    path('cart/summary/', cart_summary, name='cart_summary'),
    path('cart/clear/', clear_cart, name='clear_cart'),
    path('cart/move-to-wishlist/', move_to_wishlist, name='move_to_wishlist'),
    path('cart/validate/', validate_cart, name='validate_cart'),

    # =============================================================================
    # WISHLIST MANAGEMENT ENDPOINTS
    # =============================================================================

    # Basic wishlist operations
    path('wishlist/add/', add_to_wishlist, name='add_to_wishlist'),
    path('wishlist/remove/<uuid:product_option_id>/', remove_from_wishlist, name='remove_from_wishlist'),
    path('wishlist/', get_wishlist_items, name='get_wishlist_items'),

    # Enhanced wishlist operations
    path('wishlist/enhanced/', get_wishlist_items_enhanced, name='get_wishlist_items_enhanced'),
    path('wishlist/add-enhanced/', add_to_wishlist_enhanced, name='add_to_wishlist_enhanced'),
    path('wishlist/remove-enhanced/<uuid:product_option_id>/', remove_from_wishlist_enhanced,
         name='remove_from_wishlist_enhanced'),
    path('wishlist/move-to-cart/', move_wishlist_to_cart, name='move_wishlist_to_cart'),
    path('wishlist/share/', share_wishlist, name='share_wishlist'),
    path('wishlist/undo/', undo_wishlist_removal, name='undo_wishlist_removal'),
    path('wishlist/clear/', clear_wishlist, name='clear_wishlist'),

    # =============================================================================
    # ORDER MANAGEMENT ENDPOINTS
    # =============================================================================

    # Order tracking and management
    path('orders/<uuid:order_id>/tracking/', get_order_tracking, name='get_order_tracking'),
    path('orders/', get_user_orders, name='get_user_orders'),
    path('orders/products/<uuid:ordered_product_id>/rating/', update_order_rating, name='update_order_rating'),
    path('orders/<uuid:order_id>/cancel/', cancel_order, name='cancel_order'),

    # =============================================================================
    # PROFILE MANAGEMENT ENDPOINTS
    # =============================================================================

    # Profile operations
    path('profile/', get_profile, name='get_profile'),
    path('profile/update/', update_profile, name='update_profile'),
    path('profile/upload-image/', upload_profile_image, name='upload_profile_image'),
    path('profile/edit/', edit_profile, name='edit_profile'),

    # =============================================================================
    # ADDRESS MANAGEMENT ENDPOINTS
    # =============================================================================

    path('addresses/', get_addresses, name='get_addresses'),
    path('addresses/add/', add_address, name='add_address'),
    path('addresses/<int:address_id>/update/', update_address, name='update_address'),
    path('addresses/<int:address_id>/delete/', delete_address, name='delete_address'),

    # =============================================================================
    # BOOKING MANAGEMENT ENDPOINTS (NEW)
    # =============================================================================

    # Main booking operations
    path('bookings/', get_user_bookings, name='get_user_bookings'),
    path('bookings/<uuid:booking_id>/', get_booking_detail, name='get_booking_detail'),
    path('bookings/create/', create_booking, name='create_booking'),

    # Booking actions
    path('bookings/<uuid:booking_id>/cancel/', cancel_booking, name='cancel_booking'),
    path('bookings/<uuid:booking_id>/reschedule/', reschedule_booking, name='reschedule_booking'),
    path('bookings/<uuid:booking_id>/contact-artist/', contact_artist, name='contact_artist'),
    path('bookings/<uuid:booking_id>/book-again/', book_again, name='book_again'),

    # Rating
    path('bookings/rate/', rate_booking, name='rate_booking'),

    # Statistics
    path('bookings/stats/', get_booking_stats, name='get_booking_stats'),

    # =============================================================================
    # BEAUTY SERVICES & ARTISTS ENDPOINTS (NEW)
    # =============================================================================

    # Services
    path('services/', get_available_services, name='get_available_services'),

    # Artists
    path('artists/', get_available_artists, name='get_available_artists'),
    path('artists/<uuid:artist_id>/availability/', get_artist_availability, name='get_artist_availability'),

    path('payments/history/', get_payment_history, name='get_payment_history'),
    path('payments/summary/', get_payment_summary, name='get_payment_summary'),
    path('payments/<uuid:payment_id>/', get_payment_detail, name='get_payment_detail'),
    path('payments/<uuid:payment_id>/download-receipt/', download_receipt, name='download_receipt'),
    path('payments/<uuid:payment_id>/refund/', request_refund, name='request_refund'),
]