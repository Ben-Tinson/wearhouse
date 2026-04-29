# Staging Postgres Validation Log

Use this log while running the staging validation pass against Supabase Postgres.

Reference:

- [STAGING_POSTGRES_VALIDATION_CHECKLIST.md](/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/docs/STAGING_POSTGRES_VALIDATION_CHECKLIST.md)

## Summary

- Total blocking issues: ``
- Total high-priority issues: ``
- Total medium/low issues: ``
- Overall recommendation: `Ready / Nearly ready / Not ready`
- Test date: ``
- Tester: ``
- Staging app URL / environment notes: ``

## Auth / Session Sanity

### Login with an existing user

- Status: `Pass`
- Severity: ``
- Notes: N/A
- Follow-up action:

### Logout

- Status: `Pass`
- Severity: ``
- Notes: N/A
- Follow-up action:

### Invalid login handling

- Status: `Pass`
- Severity: ``
- Notes: N/A
- Follow-up action:

### Password reset token flow

- Status: `Fail`
- Severity: `High`
- Notes: Password reset / change-password email flow
Status: Deferred
Conclusion: token generation and verification are working correctly.
Evidence:
- token_created: True
- token_verified_user_id matches original user_id
Current blocker:
- outbound email fails with SendGrid 401 Unauthorized
- example log: Error sending email ... HTTP Error 401: Unauthorized
Likely required env/config:
- SENDGRID_API_KEY
- MAIL_DEFAULT_SENDER
- APP_BASE_URL
- PREFERRED_URL_SCHEME=https
Reason deferred:
- planned migration to Supabase Auth, so no deeper investment in the current Flask email-reset flow for now.
- Follow-up action: 

### Email confirmation / email-change confirmation

- Status: `Not tested`
- Severity: `Blocking / High / Medium / Low`
- Notes: Email not currently setup.
- Follow-up action:

## Profile / Preferences

### View profile page

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

### Update basic profile fields

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

### Update preferences

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

## Collection And Sneaker Detail

### Collection list renders

- Status: `Pass`
- Severity: ``
- Notes: Still loading quite slow when page is full, however this could be optimised at a later date when we are working on a closer to live version.
- Follow-up action:

### Sneaker detail pages render

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

### Create a new sneaker

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

### Edit an existing sneaker

- Status: `Fail`
- Severity: `Low`
- Notes: URL updates, as does sneaker model, size and others. Some things do not update on page, for example when changing the courlowar, the colourway shown in the 'release overview' card on a sneaker details page doesn't update. It is possibly prioritising data pulled from KicksDB.
- Follow-up action:

### Delete a sneaker

- Status: `Fail`
- Severity: `Low`
- Notes: Sneaker appears to be removed from collection, however marking as 'Sold' when removing does not appear to update the sold sneakers statistics for a user, that can be seen in the admin only feature at the moment.
- Follow-up action:

## Notes / Wears / Cleaning / Damage / Repair / Health

### Sneaker notes

- Status: `Pass`
- Severity: ``
- Notes: Create and delete work as expected, no edit function was created to my knowledge.
- Follow-up action:

### Wear records

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

### Cleaning events

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

### Damage events

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

### Repair events

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

### Health history / snapshots

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

## Release Pages And Market Data

### Release list / calendar

- Status: `Pass`
- Severity: ``
- Notes: Unable
- Follow-up action:

### Release detail page

- Status: `Not tested`
- Severity: `Blocking`
- Notes: Unable to test properly as data does not appear to be pulling from KicksDB properly.
- Follow-up action:

### Release pricing behavior

- Status: `Not tested`
- Severity: `Blocking`
- Notes: Unable to test properly as data does not appear to be pulling from KicksDB properly.
- Follow-up action:

### Release size bid behavior

- Status: `Not tested`
- Severity: `Blocking`
- Notes: Unable to test properly as data does not appear to be pulling from KicksDB properly.
- Follow-up action:

### Wishlist flow

- Status: `Pass`
- Severity: `Blocking`
- Notes: After failing in the first pass, appears to be working as planned now. Codex fixed it in the safest way: normalising extract() year/month values to int before formatting them. The original validation log had correctly identified this as a blocking crash on the wishlist page, and the traceback pointed straight at the month-formatting code.
- Follow-up action:

## Articles / Content

### Article list and detail pages

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

### Create and edit article content

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

### Site schema records

- Status: `Not tested`
- Severity: ``
- Notes: I am not sure how to test this
- Follow-up action:

## API Tokens

### View/manage API tokens

- Status: `Pass`
- Severity: ``
- Notes: 
- Follow-up action:

### Create a new API token

- Status: `Pass`
- Severity: ``
- Notes: 
- Follow-up action:

### Revoke a token

- Status: `Pass`
- Severity: ``
- Notes: Seems to work accordig to the UI, but could benefit from deeper testing in the future.
- Follow-up action:

## Steps / Exposure / Attribution

### Step bucket views

- Status: `Pass`
- Severity: ``
- Notes:
- Follow-up action:

### Step attribution views

- Status: `Pass`
- Severity: ``
- Notes: Hard to test procisely with no UI for this, but seems to be working.
- Follow-up action:

### Exposure event flows

- Status: `Pass`
- Severity: ``
- Notes: Whilst it passes, it is worth noting that sometimes health score can take innaccuracies. For example it showed health score as 97.9 at one point, but showed only a -3 points for rain exposure.
- Follow-up action:

### Steps/mobile sync path

- Status: `Not tested`
- Severity: ``
- Notes: Unable to test at this time. 
- Follow-up action:

## Admin / Import Flows

### Release CSV import

- Status: `Pass`
- Severity: ``
- Notes: Upload works and preview is accurate. Updating now works as planned also, after previously failing.
- Follow-up action:

### Admin release add/edit

- Status: `Pass`
- Severity: ``
- Notes: Works, but as admin whenm trying to refresh market data get this error: 
    IntegrityError
        sqlalchemy.exc.IntegrityError: (psycopg2.errors.UniqueViolation) duplicate key value violates unique constraint "uq_release_source_source_product_id"
        DETAIL:  Key (source, source_product_id)=(kicksdb_stockx, 019d30ec-8b69-7ce4-9f59-f4d9752f626d) already exists.

        [SQL: UPDATE release SET name=%(name)s, model_name=%(model_name)s, colorway=%(colorway)s, retail_price=%(retail_price)s, source=%(source)s, source_product_id=%(source_product_id)s, source_slug=%(source_slug)s, updated_at=%(updated_at)s WHERE release.id = %(release_id)s]
        [parameters: {'name': 'Nike Air Max 90 Ultramarine (2026)', 'model_name': 'Nike Air Max 90 Ultramarine (2026)', 'colorway': 'Light Base Grey/Sport Royal/Coconut Milk/White/Platinum Tint/Siren Red', 'retail_price': 150.0, 'source': 'kicksdb_stockx', 'source_product_id': '019d30ec-8b69-7ce4-9f59-f4d9752f626d', 'source_slug': 'nike-air-max-90-ultramarine-2026', 'updated_at': datetime.datetime(2026, 4, 13, 21, 3, 25, 35667), 'release_id': 10}]
        (Background on this error at: https://sqlalche.me/e/20/gkpj)

        Traceback (most recent call last)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 1963, in _exec_single_context
        self.dialect.do_execute(
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/engine/default.py", line 943, in do_execute
        
            def do_executemany(self, cursor, statement, parameters, context=None):
                cursor.executemany(statement, parameters)
        
            def do_execute(self, cursor, statement, parameters, context=None):
                cursor.execute(statement, parameters)
        
            def do_execute_no_params(self, cursor, statement, context=None):
                cursor.execute(statement)
        
            def is_disconnect(
        The above exception was the direct cause of the following exception:
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/flask/app.py", line 1536, in __call__
        return self.wsgi_app(environ, start_response)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/flask/app.py", line 1514, in wsgi_app
        response = self.handle_exception(e)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/flask/app.py", line 1511, in wsgi_app
        response = self.full_dispatch_request()
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/flask/app.py", line 919, in full_dispatch_request
        rv = self.handle_user_exception(e)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/flask/app.py", line 917, in full_dispatch_request
        rv = self.dispatch_request()
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/flask/app.py", line 902, in dispatch_request
        return self.ensure_sync(self.view_functions[rule.endpoint])(**view_args)  # type: ignore[no-any-return]
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/flask_login/utils.py", line 290, in decorated_view
        return current_app.ensure_sync(func)(*args, **kwargs)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/decorators.py", line 21, in decorated_function
        return f(*args, **kwargs)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/routes/main_routes.py", line 1663, in refresh_release_market_admin
        refreshed_release = _ensure_release_for_sku_with_resale(release.sku)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/routes/main_routes.py", line 969, in _ensure_release_for_sku_with_resale
        db.session.commit()
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/scoping.py", line 599, in commit
        return self._proxied.commit()
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 2032, in commit
        trans.commit(_to_root=True)
        File "<string>", line 2, in commit
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/state_changes.py", line 139, in _go
        ret_value = fn(self, *arg, **kw)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 1313, in commit
        self._prepare_impl()
        File "<string>", line 2, in _prepare_impl
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/state_changes.py", line 139, in _go
        ret_value = fn(self, *arg, **kw)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 1288, in _prepare_impl
        self.session.flush()
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 4345, in flush
        self._flush(objects)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 4481, in _flush
        transaction.rollback(_capture_exception=True)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/util/langhelpers.py", line 224, in __exit__
        raise exc_value.with_traceback(exc_tb)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/session.py", line 4441, in _flush
        flush_context.execute()
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/unitofwork.py", line 466, in execute
        rec.execute(self)
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/unitofwork.py", line 642, in execute
        util.preloaded.orm_persistence.save_obj(
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/persistence.py", line 85, in save_obj
        _emit_update_statements(
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/orm/persistence.py", line 912, in _emit_update_statements
        c = connection.execute(
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 1415, in execute
        return meth(
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/sql/elements.py", line 523, in _execute_on_connection
        return connection._execute_clauseelement(
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 1637, in _execute_clauseelement
        ret = self._execute_context(
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 1842, in _execute_context
        return self._exec_single_context(
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 1982, in _exec_single_context
        self._handle_dbapi_exception(
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 2351, in _handle_dbapi_exception
        raise sqlalchemy_exception.with_traceback(exc_info[2]) from e
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/engine/base.py", line 1963, in _exec_single_context
        self.dialect.do_execute(
        File "/Users/bentinson/dev/WearHouseV1/sneaker_collection_app/venv/lib/python3.9/site-packages/sqlalchemy/engine/default.py", line 943, in do_execute
        cursor.execute(statement, parameters)
        sqlalchemy.exc.IntegrityError: (psycopg2.errors.UniqueViolation) duplicate key value violates unique constraint "uq_release_source_source_product_id"
        DETAIL: Key (source, source_product_id)=(kicksdb_stockx, 019d30ec-8b69-7ce4-9f59-f4d9752f626d) already exists.

        [SQL: UPDATE release SET name=%(name)s, model_name=%(model_name)s, colorway=%(colorway)s, retail_price=%(retail_price)s, source=%(source)s, source_product_id=%(source_product_id)s, source_slug=%(source_slug)s, updated_at=%(updated_at)s WHERE release.id = %(release_id)s]
        [parameters: {'name': 'Nike Air Max 90 Ultramarine (2026)', 'model_name': 'Nike Air Max 90 Ultramarine (2026)', 'colorway': 'Light Base Grey/Sport Royal/Coconut Milk/White/Platinum Tint/Siren Red', 'retail_price': 150.0, 'source': 'kicksdb_stockx', 'source_product_id': '019d30ec-8b69-7ce4-9f59-f4d9752f626d', 'source_slug': 'nike-air-max-90-ultramarine-2026', 'updated_at': datetime.datetime(2026, 4, 13, 21, 3, 25, 35667), 'release_id': 10}]
        (Background on this error at: https://sqlalche.me/e/20/gkpj)
        The debugger caught an exception in your WSGI application. You can now look at the traceback which led to the error.
        To switch between the interactive traceback and the plaintext one, you can click on the "Traceback" headline. From the text traceback you can also create a paste of it. For code execution mouse-over the frame you want to debug and click on the console icon on the right side.

        You can execute arbitrary Python code in the stack frames and there are some extra helpers available for introspection:

        dump() shows all variables in the frame
        dump(obj) dumps all that's known about the object
- Follow-up action:

### Ingestion/update flows

- Status: `Not tested`
- Severity: ``
- Notes: I am not sure how to test this.
- Follow-up action:

### Bulk/admin content updates

- Status: `Not tested`
- Severity: ``
- Notes: Not sure how to test this
- Follow-up action:

## Postgres-Specific Watch Items

### Sequence / primary-key issues

- Status: `Not tested`
- Severity: ``
- Notes: Not sure how to test this
- Follow-up action:

### Varchar length / truncation issues

- Status: `Not tested`
- Severity: ``
- Notes: Not sure how to test this
- Follow-up action:

### Boolean / datetime / uniqueness differences

- Status: `Not tested`
- Severity: ``
- Notes: Not sure how to test this
- Follow-up action:

### Query performance / unexpected slow pages

- Status: `Not tested`
- Severity: ``
- Notes: Not sure how to test this
- Follow-up action:
