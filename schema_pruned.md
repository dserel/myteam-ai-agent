# myTeam — Pruned Schema για LLM context

Αυτό το αρχείο **δίνεται αυτούσιο στο system prompt του Claude** όταν παράγει SQL.
Κρατάμε **μόνο** τους πίνακες που χρειάζονται για: athletes/teams, events/attendance, financials, communications.

> **ΚΑΝΟΝΑΣ ΓΙΑ ΤΟ LLM**: κάθε query σου ΠΡΕΠΕΙ να φιλτράρει σε `club_id = :tenant_club_id`
> είτε άμεσα (πίνακες που έχουν `club_id`) είτε μέσω join (π.χ. teams → club_id).
> Soft deletes: όπου υπάρχει `deleted_at`, βάζεις `AND <alias>.deleted_at IS NULL`.

---

## 🧑 Athletes, Teams & Org

### `clubs`
Οι σύλλογοι. **Tenant boundary.**
- `id`, `name`, `slug`, `city`, `country_id`, `status`, `created_at`, `deleted_at`

### `users`
Όλοι οι χρήστες: athletes, parents, coaches, managers.
- `id`, `name`, `last_name`, `email`, `phone`, `birthday`, `gender`
- `role` *(tinyint — βλ. ENUMS παρακάτω)*
- `club_id` → clubs.id  *(ΠΡΟΣΟΧΗ: nullable· users που ανήκουν σε >1 club το διαχειρίζονται μέσω team_user)*
- `status` *(1=active, άλλα = inactive)*
- `created_at`, `deleted_at`, `web_login_at`, `mob_login_at`
- `household_id` → households.id *(οικογένεια)*

### `teams`
Οι ομάδες ενός club.
- `id`, `name`, `sport`, `category` *(m/f/mixed)*
- `club_id` → clubs.id
- `season_id` → seasons.id, `division_id` → divisions.id
- `status` *(1=active)*, `created_at`

### `team_user`
Pivot: ποιοι αθλητές/coaches σε ποια ομάδα.
- `team_id` → teams.id, `user_id` → users.id
- `first_coach` *(1 = πρώτος προπονητής, 0 = αθλητής ή assistant)*
- `number` *(φανέλα)*
- `created_at`, `deleted_at`
- **⚠ Το `created_at` εδώ συχνά είναι NULL — μην το εμπιστεύεσαι για "πότε μπήκε στην ομάδα". Για χρονικές ερωτήσεις προτίμα το `users.created_at`.**

### `parent_users`
Pivot γονιού-παιδιού.
- `parent_id` → users.id *(ο γονιός)*
- `user_id` → users.id *(το παιδί/αθλητής)*
- `status` *(varchar, '1' = active)*
- `gets_notifications`

### `seasons`
- `id`, `name`, `current` *(1 = τρέχουσα)*, `club_id`, `start_date`, `end_date`

### `divisions`
- `id`, `name` *(π.χ. "Παμπαίδες Α"), `club_id`

### `households`
Νοικοκυριά (μοιραζόμενα στοιχεία οικογένειας).
- `id`, `club_id`, στοιχεία διεύθυνσης

---

## 📅 Events & Attendance

### `events`
Προπονήσεις, αγώνες, εκδηλώσεις.
- `id`, `title`, `comments`
- `start_date`, `end_date` *(datetime)*
- `type` *(tinyint — βλ. ENUMS)*
- `field_id` → fields.id, `location_alias`
- `club_id` → clubs.id, `created_by` → users.id
- `result` *(1=win, 2=loss, 3=draw — για αγώνες)*, `score_home`, `score_away`
- `created_at`, `deleted_at`

### `eventables`
Polymorphic: ποιος συμμετέχει σε event.
- `event_id` → events.id
- `eventable_type` *(string — Laravel morph: π.χ. `'App\\Models\\Team'` ή `'App\\Models\\User'`. **ΕΠΙΒΕΒΑΙΩΣΕ τις τιμές με `SELECT DISTINCT eventable_type FROM eventables`.**)*
- `eventable_id` *(team_id ή user_id ανάλογα)*

### `appearance_events`
Παρουσίες σε events.
- `id`, `user_id`, `team_id`, `event_id`
- `check` *(1 = παρών, 0 = απών, NULL = δεν καταγράφηκε)*
- `status` *(tinyint — π.χ. justified absence)*
- `notes`, `created_at`

### `fields`
Γήπεδα/χώροι.
- `id`, `name`, `club_id`

---

## 💶 Financials

### `incomes`
Έσοδα του club.
- `id`, `transaction_at` *(date)*, `amount`, `notes`
- `type` *(varchar — π.χ. "subscription", "donation")*
- `payment_type` *(cash/card/transfer)*
- `club_id` → clubs.id
- `user_id` → users.id *(ποιον αφορά)*
- `income_type_id` → income_types.id
- `subscription_id`, `payment_id`, `purchase_id`, `donation_id` *(source link)*
- `received_from`, `created_at`, `deleted_at`

### `outgoings`
Έξοδα του club.
- `id`, `transaction_at`, `amount`, `notes`
- `type`, `payment_type`
- `club_id` → clubs.id
- `supplier_id` → suppliers.id, `expense_type_id` → expense_types.id
- `created_at`, `deleted_at`

### `payments`
Πληρωμές που έκαναν χρήστες.
- `id`, `transaction_at`, `amount`, `payment_method` *(smallint)*
- `user_id` → users.id, `subscription_id` → subscriptions.id
- `online_payment_id` → online_payments.id *(αν online)*
- `created_at`, `deleted_at`
- **⚠ Δεν έχει `club_id` — φιλτράρισμα μέσω `users.club_id` ή `subscriptions.club_id`.**

### `subscriptions`
Πακέτα συνδρομής (όχι instances).
- `id`, `title`, `amount`, `billing_cycle`, `interval`
- `start_date`, `end_date`, `due_at`
- `free`, `one_time_payment`, `recurring`
- `status` *(1=active)*
- `club_id` → clubs.id
- `subscription_template_id` → subscription_templates.id

### `subscription_users`
Pivot: ποιος χρήστης πάνω σε ποια συνδρομή.
- `user_id`, `subscription_id`
- `subscription_at` *(date έναρξης)*, `due_at` *(date λήξης)*
- `total_paid`
- **⚠ Φιλτράρισμα μέσω `subscriptions.club_id`.**

### `recurring_subscriptions`
Online recurring (Stripe).
- `id`, `club_id`, `user_id`, `subscription_id`, status flags

### `income_types`, `expense_types`
Κατηγορίες λογιστικής.
- `id`, `name`, `club_id`

### `suppliers`
Προμηθευτές για outgoings.
- `id`, `name`, `club_id`

---

## 📣 Communications

### `posts`
Ανακοινώσεις/άρθρα.
- `id`, `title`, `content`, `show_from`, `show_to`
- `user_id` *(συγγραφέας)*, `club_id` → clubs.id
- `fromAdmin` *(1 = από διαχείριση myTeam, όχι από club)*
- `created_at`

### `post_team`
Σύνδεση post → συγκεκριμένες ομάδες.
- `post_id`, `team_id`

### `polls`
Ερωτήσεις/ψηφοφορίες.
- `id`, `question`, `status` *(enum '1','2','3')*
- `start_date`, `end_date`, `user_id` *(δημιουργός)*
- `roles` *(CSV ρόλων που μπορούν να ψηφίσουν)*
- **⚠ Δεν έχει `club_id` — φιλτράρισμα μέσω `polls.user_id → users.club_id` ή μέσω `pollable`.**

### `answers`
Απαντήσεις χρηστών σε polls.
- `id`, `poll_id`, `user_id`, `answer`

### `pollable`
Polymorphic σύνδεση poll → club/team.
- `poll_id`, `pollable_type`, `pollable_id`

### `notifications`
Push notifications (Laravel default schema).
- `id` *(uuid)*, `type`, `notifiable_type`, `notifiable_id`
- `data` *(JSON text)*, `read_at`, `created_at`

---

## 🔐 Roles & permissions

### `roles`
- `id`, `name` *(π.χ. "manager", "athlete", "parent", "coach")*, `club_id`

### `model_has_roles`
- `role_id` → roles.id
- `model_type` *(π.χ. `'App\\Models\\User'`)*
- `model_id` *(users.id)*

---

## 📊 ENUMS / lookup values

```
users.role (tinyint):
  ⚠ Επιβεβαίωσε με: SELECT role, COUNT(*) FROM users GROUP BY role
  Πιθανές τιμές (εικασία): 1=manager, 2=coach, 3=parent, 4=athlete, 5=admin

events.type (tinyint):
  ⚠ Επιβεβαίωσε με: SELECT type, COUNT(*) FROM events GROUP BY type
  Πιθανές: 1=match, 2=tournament, 3=training, 4=meeting, 5=event

events.result (tinyint):
  1=win, 2=loss, 3=draw, NULL=μη αγώνας ή χωρίς αποτέλεσμα

teams.category (char(2)):
  'm'=ανδρικό, 'f'=γυναικείο, 'mx'=μεικτό

teams.status (int): 1=active, άλλα=archived
users.status (tinyint): 1=active, άλλα=inactive
subscriptions.status (tinyint): 1=active

appearance_events.check (tinyint(1)):
  1=παρών, 0=απών, NULL=δεν καταγράφηκε

polls.status (enum): '1'=draft, '2'=active, '3'=closed

payments.payment_method (smallint):
  ⚠ Επιβεβαίωσε. Πιθανές: 1=cash, 2=card, 3=transfer, 4=online
```

---

## 🚫 Πίνακες που σκόπιμα ΛΕΙΠΟΥΝ από αυτό το context

(παρόντες στη βάση αλλά μη σχετικοί για το MVP)

- Marketplace: `products`, `sellers`, `purchases`, `affiliates`, `b2b_*`
- Storage: `storages`, `storage_*`, `team_storage`, `sport_storage`
- Forms/evaluations: `forms`, `evaluations`, `evaluation_*`
- Misc: `tutorials`, `tags`, `taggable`, `categorizables`, `revisions`, `versions`, `jobs`, `failed_jobs`, `sessions`, `password_resets`, `fcm_tokens`, `personal_access_tokens`

Αν χρειαστείς κάτι από αυτά, **πες το στον χρήστη — μην το αυτοσχεδιάσεις.**
