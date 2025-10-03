from django.shortcuts import render
from django.http import HttpResponseRedirect


def password_protected_view(password):
    def decorator(view_func):
        def _wrapped_view(self, request, *args, **kwargs):
            session_key = f'passed_{self.model._meta.model_name}_auth'
            model_name = self.model._meta.verbose_name.title()  # Get the model's verbose name

            error_message = None

            if request.session.get(session_key):
                return view_func(self, request, *args, **kwargs)
            elif request.method == 'POST':
                if request.POST.get('password') == password:
                    request.session[session_key] = True
                    return HttpResponseRedirect(request.get_full_path())
                else:
                    error_message = 'Incorrect password!'

            return render(request, 'admin/password_prompt.html', {'error_message': error_message, 'model_name': model_name})

        return _wrapped_view
    return decorator
