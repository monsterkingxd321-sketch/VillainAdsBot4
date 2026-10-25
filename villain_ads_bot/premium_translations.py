"""
Premium UI strings.

The premium card based interface has its own catalogue so the (large) legacy
`translations.py` never has to be touched. `utils.get_text()` resolves the
premium keys first and falls back to the legacy catalogue.
"""

LANGS = ("en", "hi", "ru", "ja", "fr", "de", "zh", "ar")

PREMIUM_TRANSLATIONS = {
    "premium_main_title": {
        "en": "Control Panel", "hi": "कंट्रोल पैनल", "ru": "Панель управления",
        "ja": "コントロールパネル", "fr": "Panneau de contrôle",
        "de": "Steuerungspanel", "zh": "控制面板", "ar": "لوحة التحكم",
    },
    "premium_main_sub": {
        "en": "Premium UserBot Manager", "hi": "प्रीमियम यूज़रबॉट मैनेजर",
        "ru": "Премиум менеджер юзерботов", "ja": "プレミアムユーザボットマネージャー",
        "fr": "Gestionnaire de userbots premium", "de": "Premium Userbot-Manager",
        "zh": "高级用户机器人管理器", "ar": "مدير يوزربوت مميز",
    },
    "premium_main_hint": {
        "en": "Add an account, open its dashboard and control broadcasts, welcome messages, VC playback and more.",
        "hi": "अकाउंट जोड़ें, उसका डैशबोर्ड खोलें और ब्रॉडकास्ट, वेलकम संदेश, VC प्लेबैक आदि कंट्रोल करें।",
        "ru": "Добавьте аккаунт, откройте панель и управляйте рассылками, приветствиями, VC и не только.",
        "ja": "アカウントを追加して、ダッシュボードから配信・挨拶・VC再生などを管理できます。",
        "fr": "Ajoutez un compte et pilotez diffusions, messages de bienvenue, VC et plus encore.",
        "de": "Konto hinzufügen und Rundsendungen, Willkommensnachrichten, VC u.v.m. steuern.",
        "zh": "添加账户，通过面板控制广播、欢迎消息、语音聊天等。",
        "ar": "أضف حسابًا وتحكم في البث والرسائل الترحيبية وVC والمزيد.",
    },
    "premium_bots_title": {
        "en": "Connected Userbots", "hi": "जुड़े हुए यूज़रबॉट", "ru": "Подключённые юзерботы",
        "ja": "接続済みユーザボット", "fr": "Userbots connectés", "de": "Verbundene Userbots",
        "zh": "已连接的用户机器人", "ar": "اليوزربوتات المتصلة",
    },
    "premium_bots_sub": {
        "en": "Your active Telegram accounts", "hi": "आपके सक्रिय टेलीग्राम अकाउंट",
        "ru": "Ваши активные Telegram-аккаунты", "ja": "アクティブな Telegram アカウント",
        "fr": "Vos comptes Telegram actifs", "de": "Ihre aktiven Telegram-Konten",
        "zh": "您活跃的 Telegram 账号", "ar": "حسابات تيليجرام النشطة",
    },
    "premium_bots_hint": {
        "en": "Select an account below to open its control panel, or tap All Slots for the bulk dashboard.",
        "hi": "नीचे से अकाउंट चुनें और उसका कंट्रोल पैनल खोलें, या सभी के लिए All Slots दबाएं।",
        "ru": "Выберите аккаунт ниже или нажмите All Slots для панели всех аккаунтов.",
        "ja": "下のアカウントを選ぶか、All Slots で一括ダッシュボードを開きます。",
        "fr": "Sélectionnez un compte ci-dessous ou touchez All Slots pour le tableau de bord global.",
        "de": "Wähle unten ein Konto oder tippe auf All Slots für das Sammel-Dashboard.",
        "zh": "在下方选择账号，或点击 All Slots 打开批量面板。",
        "ar": "اختر حسابًا أدناه أو اضغط All Slots للوحة التحكم الجماعية.",
    },
    "premium_slots_title": {
        "en": "All Slots Control Dashboard", "hi": "सभी स्लॉट कंट्रोल डैशबोर्ड",
        "ru": "Панель управления всеми слотами", "ja": "全スロットの操作ダッシュボード",
        "fr": "Tableau de bord de tous les emplacements", "de": "Dashboard für alle Slots",
        "zh": "所有槽位控制面板", "ar": "لوحة التحكم بكل الخانات",
    },
    "premium_slots_sub": {
        "en": "Bulk control centre", "hi": "बल्क कंट्रोल सेंटर", "ru": "Центр массового управления",
        "ja": "一括操作センター", "fr": "Centre de contrôle groupé", "de": "Bulk-Kontrollzentrum",
        "zh": "批量控制中心", "ar": "مركز التحكم الجماعي",
    },
    "premium_settings_title": {
        "en": "User Settings", "hi": "यूज़र सेटिंग्स", "ru": "Настройки пользователя",
        "ja": "ユーザー設定", "fr": "Paramètres utilisateur", "de": "Benutzereinstellungen",
        "zh": "用户设置", "ar": "إعدادات المستخدم",
    },
    "premium_settings_sub": {
        "en": "Account, wallet and language", "hi": "अकाउंट, वॉलेट और भाषा",
        "ru": "Аккаунт, кошелёк и язык", "ja": "アカウント・ウォレット・言語",
        "fr": "Compte, portefeuille et langue", "de": "Konto, Wallet und Sprache",
        "zh": "账户、钱包和语言", "ar": "الحساب والمحفظة واللغة",
    },
    "premium_status_title": {
        "en": "System Status", "hi": "सिस्टम स्टेटस", "ru": "Статус системы",
        "ja": "システム状況", "fr": "État du système", "de": "Systemstatus",
        "zh": "系统状态", "ar": "حالة النظام",
    },
    "premium_status_sub": {
        "en": "Live overview of your automation", "hi": "आपके ऑटोमेशन का लाइव अवलोकन",
        "ru": "Обзор вашей автоматизации", "ja": "自動化の概要", "fr": "Aperçu de votre automatisation",
        "de": "Überblick deiner Automatisierung", "zh": "自动化运行概览", "ar": "نظرة على أتمتتك",
    },
    "payment_method_title": {
        "en": "Select Payment Method", "hi": "भुगतान विधि चुनें", "ru": "Выберите способ оплаты",
        "ja": "支払い方法を選択", "fr": "Choisissez le mode de paiement", "de": "Zahlungsmethode wählen",
        "zh": "选择支付方式", "ar": "اختر طريقة الدفع",
    },
    "payment_method_sub": {
        "en": "Review the amount and choose how you want to pay", "hi": "राशि देखें और भुगतान का तरीका चुनें",
        "ru": "Проверьте сумму и выберите способ оплаты", "ja": "金額を確認して支払い方法を選んでください",
        "fr": "Vérifiez le montant et choisissez votre mode de paiement", "de": "Betrag prüfen und Zahlungsart wählen",
        "zh": "确认金额并选择支付方式", "ar": "راجع المبلغ واختر طريقة الدفع",
    },
    "payment_total_inr": {
        "en": "Total (INR)", "hi": "कुल (INR)", "ru": "Итого (INR)", "ja": "合計 (INR)",
        "fr": "Total (INR)", "de": "Gesamt (INR)", "zh": "总计 (INR)", "ar": "الإجمالي (INR)",
    },
    "payment_total_usdt": {
        "en": "Total (USDT)", "hi": "कुल (USDT)", "ru": "Итого (USDT)", "ja": "合計 (USDT)",
        "fr": "Total (USDT)", "de": "Gesamt (USDT)", "zh": "总计 (USDT)", "ar": "الإجمالي (USDT)",
    },
    "payment_total_ton": {
        "en": "Total (TON)", "hi": "कुल (TON)", "ru": "Итого (TON)", "ja": "合計 (TON)",
        "fr": "Total (TON)", "de": "Gesamt (TON)", "zh": "总计 (TON)", "ar": "الإجمالي (TON)",
    },
    "payment_wallet": {
        "en": "Wallet balance", "hi": "वॉलेट बैलेंस", "ru": "Баланс кошелька", "ja": "ウォレット残高",
        "fr": "Solde du portefeuille", "de": "Wallet-Guthaben", "zh": "钱包余额", "ar": "رصيد المحفظة",
    },
    "payment_reference": {
        "en": "Reference", "hi": "संदर्भ", "ru": "Ссылка", "ja": "参照番号", "fr": "Référence",
        "de": "Referenz", "zh": "参考编号", "ar": "المرجع",
    },
    "payment_utr": {
        "en": "UTR / TxID", "hi": "UTR / TxID", "ru": "UTR / TxID", "ja": "UTR / TxID",
        "fr": "UTR / TxID", "de": "UTR / TxID", "zh": "UTR / TxID", "ar": "UTR / TxID",
    },
    "payment_screenshot_hint": {
        "en": "Now send your payment screenshot (photo or image link).",
        "hi": "अब अपने भुगतान का स्क्रीनशॉट भेजें (फोटो या इमेज लिंक)।",
        "ru": "Отправьте скриншот оплаты (фото или ссылку на изображение).",
        "ja": "決済のスクリーンショット（画像またはリンク）を送信してください。",
        "fr": "Envoyez la capture de votre paiement (photo ou lien d'image).",
        "de": "Sende jetzt den Screenshot der Zahlung (Foto oder Bild-Link).",
        "zh": "请发送付款截图（照片或图片链接）。", "ar": "أرسل الآن لقطة شاشة الدفع (صورة أو رابط).",
    },
    "payment_submitted": {
        "en": "Payment submitted successfully", "hi": "भुगतान सफलतापूर्वक जमा हुआ",
        "ru": "Платёж успешно отправлен", "ja": "支払いが送信されました",
        "fr": "Paiement envoyé avec succès", "de": "Zahlung erfolgreich gesendet",
        "zh": "支付已成功提交", "ar": "تم إرسال الدفع بنجاح",
    },
    "vc_menu_title": {
        "en": "VC, Groups & Music", "hi": "वीसी, ग्रुप्स और म्यूज़िक", "ru": "VC, группы и музыка",
        "ja": "VC・グループ・音楽", "fr": "VC, Groupes et Musique", "de": "VC, Gruppen & Musik",
        "zh": "语音、群组与音乐", "ar": "VC والمجموعات والموسيقى",
    },
    "vc_section_join": {
        "en": "VC Join", "hi": "VC जॉइन", "ru": "Подключение к VC", "ja": "VC 参加",
        "fr": "Rejoindre le VC", "de": "VC beitreten", "zh": "加入语音", "ar": "الانضمام إلى VC",
    },
    "vc_section_playing": {
        "en": "VC Playing", "hi": "VC प्लेइंग", "ru": "Воспроизведение в VC", "ja": "VC 再生",
        "fr": "Lecture dans le VC", "de": "VC-Wiedergabe", "zh": "语音播放", "ar": "تشغيل في VC",
    },
    "vc_section_group": {
        "en": "Group Joining & Leaving", "hi": "ग्रुप जॉइन और लीव",
        "ru": "Вход и выход из групп", "ja": "グループの参加と退出",
        "fr": "Rejoindre et quitter des groupes", "de": "Gruppen beitreten und verlassen",
        "zh": "加入与退出群组", "ar": "الانضمام والمغادرة من المجموعات",
    },
    "timing_title": {
        "en": "Timing & Delay Settings", "hi": "टाइमिंग और डिले सेटिंग्स",
        "ru": "Настройки тайминга и задержки", "ja": "タイミングと遅延設定",
        "fr": "Réglages de délai", "de": "Timing- & Verzögerungseinstellungen",
        "zh": "时间与延迟设置", "ar": "إعدادات التوقيت والتأخير",
    },
    "timing_real_period": {
        "en": "REAL period between broadcasts", "hi": "ब्रॉडकास्ट के बीच असली अवधि",
        "ru": "РЕАЛЬНЫЙ период между рассылками", "ja": "配信の実際の間隔",
        "fr": "Période RÉELLE entre les diffusions", "de": "ECHTE Periode zwischen Rundsendungen",
        "zh": "广播之间的真实间隔", "ar": "الفترة الحقيقية بين البثات",
    },
    "timing_round_duration": {
        "en": "Round duration", "hi": "एक राउंड की अवधि", "ru": "Длительность раунда",
        "ja": "1ラウンドの所要時間", "fr": "Durée d'un tour", "de": "Runden-Dauer",
        "zh": "一轮耗时", "ar": "مدة الجولة",
    },
    "timing_repeat_interval": {
        "en": "Repeat interval", "hi": "रिपीट अंतराल", "ru": "Интервал повтора",
        "ja": "繰り返し間隔", "fr": "Intervalle de répétition", "de": "Wiederholungsintervall",
        "zh": "重复间隔", "ar": "فترة التكرار",
    },
    "timing_group_delay": {
        "en": "Group delay", "hi": "ग्रुप डिले", "ru": "Задержка между группами",
        "ja": "グループ間の遅延", "fr": "Délai entre les groupes", "de": "Verzögerung zwischen Gruppen",
        "zh": "群组间延迟", "ar": "التأخير بين المجموعات",
    },
    "coupon_redeemed": {
        "en": "Coupon Redeemed!", "hi": "कूपन रिडीम हो गया!", "ru": "Купон активирован!",
        "ja": "クーポンを使用しました！", "fr": "Coupon activé !", "de": "Gutschein eingelöst!",
        "zh": "优惠券已兑换！", "ar": "تم استخدام القسيمة!",
    },
    "coupon_invalid": {
        "en": "Invalid Coupon", "hi": "अमान्य कूपन", "ru": "Неверный купон", "ja": "無効なクーポン",
        "fr": "Coupon invalide", "de": "Ungültiger Gutschein", "zh": "无效优惠券", "ar": "قسيمة غير صالحة",
    },
    "coupon_used_up": {
        "en": "Coupon Used Up", "hi": "कूपन की सीमा समाप्त", "ru": "Лимит купона исчерпан",
        "ja": "クーポンの使用回数を超えました", "fr": "Coupon épuisé", "de": "Gutschein aufgebraucht",
        "zh": "优惠券已用完", "ar": "نفدت القسيمة",
    },
    "coupon_already_used": {
        "en": "Already Used", "hi": "पहले ही उपयोग हो चुका है", "ru": "Уже использован",
        "ja": "既に使用済み", "fr": "Déjà utilisé", "de": "Bereits eingelöst",
        "zh": "已使用过", "ar": "تم استخدامه بالفعل",
    },
    "roles_title": {
        "en": "Admin Roles", "hi": "एडमिन भूमिकाएँ", "ru": "Роли администраторов", "ja": "管理者権限",
        "fr": "Rôles des administrateurs", "de": "Administratorrollen", "zh": "管理员角色",
        "ar": "أدوار المشرفين",
    },
    "roles_owner": {
        "en": "Owner", "hi": "ओनर", "ru": "Владелец", "ja": "オーナー", "fr": "Propriétaire",
        "de": "Eigentümer", "zh": "所有者", "ar": "المالك",
    },
    "roles_moderator": {
        "en": "Moderator", "hi": "मॉडरेटर", "ru": "Модератор", "ja": "モデレーター",
        "fr": "Modérateur", "de": "Moderator", "zh": "审核员", "ar": "المشرف",
    },
    "roles_support": {
        "en": "Support", "hi": "सपोर्ट", "ru": "Поддержка", "ja": "サポート",
        "fr": "Support", "de": "Support", "zh": "客服", "ar": "الدعم",
    },
    "audit_title": {
        "en": "Audit Log", "hi": "ऑडिट लॉग", "ru": "Журнал аудита", "ja": "監査ログ",
        "fr": "Journal d'audit", "de": "Audit-Protokoll", "zh": "审计日志", "ar": "سجل التدقيق",
    },
    "backup_title": {
        "en": "Backup & Restore", "hi": "बैकअप और रिस्टोर", "ru": "Резервная копия и восстановление",
        "ja": "バックアップと復元", "fr": "Sauvegarde et restauration", "de": "Backup & Wiederherstellung",
        "zh": "备份与恢复", "ar": "النسخ الاحتياطي والاستعادة",
    },
}


def get_premium_text(key: str, lang: str = "en") -> str:
    """Returns the premium string for a key (or None when it does not exist)."""
    entry = PREMIUM_TRANSLATIONS.get(key)
    if not entry:
        return None
    return entry.get(lang) or entry.get("en")
