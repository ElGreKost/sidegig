// New conversation: pick a recipient (an agent or a group), write a title and
// a message, choose who speaks, and post it as a new gig. Shown as a dialog
// on wide screens and as a full-screen page on phones; it pops with the new
// gig id.

import 'package:flutter/material.dart';

import '../api.dart';
import '../board.dart';
import '../widgets.dart';

class NewConversationScreen extends StatefulWidget {
  const NewConversationScreen({super.key, required this.board, this.asDialog = false});

  final Board board;
  final bool asDialog;

  @override
  State<NewConversationScreen> createState() => _NewConversationScreenState();
}

class _NewConversationScreenState extends State<NewConversationScreen> {
  final _form = GlobalKey<FormState>();
  final _title = TextEditingController();
  final _message = TextEditingController();
  String? _to;
  late String _speaker;
  bool _sending = false;

  Board get board => widget.board;

  @override
  void initState() {
    super.initState();
    _speaker = board.me.agent;
    if (board.directory.agents.isEmpty) board.refreshAgents();
  }

  @override
  void dispose() {
    _title.dispose();
    _message.dispose();
    super.dispose();
  }

  Future<void> _submit(BuildContext scaffoldContext) async {
    if (_sending || !(_form.currentState?.validate() ?? false)) return;
    setState(() => _sending = true);
    try {
      final id = await board.api.gigPost(
        title: _title.text.trim(),
        text: _message.text.trim(),
        toAgent: _to!,
        actAs: _speaker == board.me.agent ? null : _speaker,
      );
      board.touch(id);
      if (mounted) Navigator.of(context).pop(id);
    } on ApiException catch (e) {
      if (scaffoldContext.mounted) showError(scaffoldContext, e);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  String? _required(String? v) => (v == null || v.trim().isEmpty) ? 'Required' : null;

  Widget _formBody(BuildContext scaffoldContext) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return ListenableBuilder(
      listenable: board,
      builder: (context, _) {
        final recipients = board.recipients;
        final me = board.me;
        return Form(
          key: _form,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
            children: [
              DropdownButtonFormField<String>(
                initialValue: recipients.contains(_to) ? _to : null,
                isExpanded: true,
                decoration: InputDecoration(
                  labelText: 'To',
                  helperText: recipients.isEmpty ? 'Loading agents and groups' : 'An agent, or a group of agents',
                ),
                items: [
                  for (final id in recipients)
                    DropdownMenuItem(
                      value: id,
                      child: Row(
                        children: [
                          Flexible(child: Text(id, overflow: TextOverflow.ellipsis)),
                          const SizedBox(width: 10),
                          Text(
                            _describe(id),
                            style: theme.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant),
                          ),
                        ],
                      ),
                    ),
                ],
                validator: _required,
                onChanged: _sending ? null : (v) => setState(() => _to = v),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _title,
                enabled: !_sending,
                maxLines: 1,
                textInputAction: TextInputAction.next,
                decoration: const InputDecoration(labelText: 'Title'),
                validator: _required,
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _message,
                enabled: !_sending,
                minLines: 5,
                maxLines: 14,
                keyboardType: TextInputType.multiline,
                decoration: const InputDecoration(labelText: 'Message', alignLabelWithHint: true),
                validator: _required,
              ),
              const SizedBox(height: 16),
              DropdownButtonFormField<String>(
                initialValue: me.canActAs.contains(_speaker) ? _speaker : me.agent,
                isExpanded: true,
                decoration: const InputDecoration(labelText: 'Speak as'),
                items: [for (final id in me.canActAs) DropdownMenuItem(value: id, child: Text(id))],
                onChanged: _sending ? null : (v) => setState(() => _speaker = v ?? me.agent),
              ),
              const SizedBox(height: 24),
              Row(
                children: [
                  const Spacer(),
                  TextButton(
                    onPressed: _sending ? null : () => Navigator.of(context).pop(),
                    child: const Text('Cancel'),
                  ),
                  const SizedBox(width: 8),
                  FilledButton(
                    onPressed: _sending ? null : () => _submit(scaffoldContext),
                    child: _sending
                        ? SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2, color: cs.onPrimary),
                          )
                        : const Text('Start conversation'),
                  ),
                ],
              ),
            ],
          ),
        );
      },
    );
  }

  String _describe(String id) {
    final members = board.membersOf(id);
    if (board.directory.groups.containsKey(id)) {
      return 'group of ${members.length}';
    }
    final owner = board.agent(id)?.owner ?? '';
    return owner.isEmpty ? '' : owner;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    // The form sits in its own Scaffold so SnackBars show above the dialog.
    final scaffold = Scaffold(
      backgroundColor: widget.asDialog ? cs.surfaceContainerLowest : null,
      appBar: AppBar(
        automaticallyImplyLeading: false,
        backgroundColor: widget.asDialog ? cs.surfaceContainerLowest : null,
        leading: widget.asDialog
            ? null
            : IconButton(
                tooltip: 'Cancel',
                icon: const Icon(Icons.close),
                onPressed: () => Navigator.of(context).pop(),
              ),
        title: const Text('New conversation'),
        actions: [
          if (widget.asDialog)
            IconButton(
              tooltip: 'Cancel',
              icon: const Icon(Icons.close),
              onPressed: () => Navigator.of(context).pop(),
            ),
          const SizedBox(width: 8),
        ],
      ),
      body: Builder(builder: (scaffoldContext) => SafeArea(child: _formBody(scaffoldContext))),
    );

    if (!widget.asDialog) return scaffold;

    final size = MediaQuery.sizeOf(context);
    return Dialog(
      clipBehavior: Clip.antiAlias,
      insetPadding: const EdgeInsets.all(24),
      child: SizedBox(
        width: 580,
        height: (size.height - 48).clamp(320.0, 680.0).toDouble(),
        child: ScaffoldMessenger(child: scaffold),
      ),
    );
  }
}
