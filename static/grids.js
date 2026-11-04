/* SplitMate AG Grid layer: the allocation matrix and the payment ledger.
 *
 * The grids present and edit browser state only. Split math stays on the server
 * and every money action stays on the dashboard's share rows, so nothing here can
 * create, capture or retry a payment. If the bundle fails to load, available()
 * is false and app.js falls back to its plain editor.
 */
(function (root) {
  'use strict';

  const PINNED_KINDS = { share: 'Fair share', paid: 'Fronted', net: 'Net position' };
  const instances = { matrix: null, matrixNames: '', ledger: null };

  function available() {
    return !!root.agGrid && typeof root.agGrid.createGrid === 'function';
  }

  function theme() {
    return root.agGrid.themeQuartz.withParams({
      backgroundColor: '#0f1728',
      foregroundColor: '#e8eef8',
      headerBackgroundColor: '#111a2c',
      headerTextColor: '#93a4bd',
      oddRowBackgroundColor: '#0e1729',
      borderColor: '#23314b',
      accentColor: '#00d4a0',
      browserColorScheme: 'dark',
      fontFamily: 'inherit',
      fontSize: 13.5,
      headerFontSize: 12,
      headerFontWeight: 700,
      rowHeight: 42,
      headerHeight: 40,
      wrapperBorderRadius: 12,
    });
  }

  /* ------------------------------------------------------------ pure helpers */

  /** Parse a typed amount into a canonical "12.34" string, or null if invalid. */
  function parseAmountInput(raw) {
    const text = String(raw == null ? '' : raw).replace(/[\s,$€£]/g, '');
    if (!/^\d+(\.\d{1,2})?$/.test(text)) return null;
    const cents = Math.round(Number(text) * 100);
    if (!Number.isSafeInteger(cents) || cents <= 0) return null;
    return (cents / 100).toFixed(2);
  }

  function amountCents(text) {
    const value = parseAmountInput(text);
    return value === null ? 0 : Math.round(Number(value) * 100);
  }

  /** Neutralise spreadsheet formula injection in exported names and notes. */
  function csvSafe(value) {
    const text = String(value == null ? '' : value);
    return /^[=+\-@\t\r]/.test(text) ? "'" + text : text;
  }

  function signedMoney(cents, currency, money) {
    const sign = cents < 0 ? '−' : cents > 0 ? '+' : '';
    return sign + money(Math.abs(cents), currency);
  }

  /** The three reconciliation rows pinned under the matrix. */
  function pinnedRows(split, participants) {
    if (!split) return [];
    const byName = {};
    (split.participants || []).forEach((person) => { byName[person.name] = person; });
    const column = (key) => {
      const values = {};
      participants.forEach((name) => {
        const person = byName[String(name).trim()];
        values[name] = person ? person[key] : 0;
      });
      return values;
    };
    const sum = (key) => (split.participants || []).reduce((total, person) => total + person[key], 0);
    return [
      { uid: 'pinned-share', pinnedKind: 'share', description: PINNED_KINDS.share, total_cents: sum('share_cents'), values: column('share_cents') },
      { uid: 'pinned-paid', pinnedKind: 'paid', description: PINNED_KINDS.paid, total_cents: sum('paid_cents'), values: column('paid_cents') },
      { uid: 'pinned-net', pinnedKind: 'net', description: PINNED_KINDS.net, total_cents: sum('net_cents'), values: column('net_cents') },
    ];
  }

  /** Facts for the strip above the matrix, all derived from the server's split. */
  function reconciliation(split, editor) {
    const expenses = (editor && editor.expenses) || [];
    const missingPayers = expenses.filter((expense) => !expense.paid_by).length;
    if (!split) return { ready: false, missingPayers, allocated: 0, total: 0, netSum: null, orders: 0, collecting: 0 };
    const allocated = (split.expenses || []).reduce(
      (total, row) => total + Object.values(row.shares || {}).reduce((sum, cents) => sum + cents, 0), 0);
    const netSum = (split.participants || []).reduce((total, person) => total + person.net_cents, 0);
    return {
      ready: missingPayers === 0 && allocated === split.total_cents && netSum === 0,
      missingPayers,
      allocated,
      total: split.total_cents,
      netSum,
      orders: (split.orders || []).length,
      collecting: (split.totals && split.totals.orders_cents) || 0,
    };
  }

  /** Project dashboard shares into ledger rows, keeping checkout URLs out of the grid and its CSV. */
  function ledgerRows(event, statusLabel, captureHeld) {
    return (event.shares || []).map((share) => {
      const isCredit = share.status === 'credit';
      const isSettled = share.status === 'settled';
      return {
        name: share.name,
        position: isCredit ? 'is owed' : isSettled ? 'settled' : 'owes',
        amount_cents: Math.abs((isCredit ? share.net_cents : share.amount_cents) || 0),
        status_key: share.status,
        status: statusLabel(share.status, captureHeld(share)),
        order_id: share.order_id || '',
        capture_id: share.capture_id || '',
        capture_status: share.capture_status || '',
        last_error: share.last_error || '',
      };
    });
  }

  function isBody(params) {
    return !!params.data && !params.data.pinnedKind;
  }

  /* --------------------------------------------------------- allocation matrix */

  function personColumn(name, context) {
    const key = String(name).trim();
    return {
      colId: 'person:' + name,
      headerName: name,
      headerTooltip: 'Click a cell or press Space to include or exclude ' + name,
      minWidth: 104,
      flex: 1,
      type: 'rightAligned',
      sortable: false,
      enableCellChangeFlash: true,
      valueGetter: (params) => {
        const data = params.data;
        if (!data) return null;
        if (data.pinnedKind) return data.values ? data.values[name] : null;
        if (!data.shared_by.includes(name)) return null;
        return data.shares && data.shares[key] != null ? data.shares[key] : undefined;
      },
      valueFormatter: (params) => {
        const data = params.data;
        if (!data) return '';
        const currency = context.currency();
        if (data.pinnedKind === 'net') return signedMoney(params.value || 0, currency, context.money);
        if (data.pinnedKind) return context.money(params.value || 0, currency);
        if (!data.shared_by.includes(name)) return 'not in';
        return params.value === undefined ? '…' : context.money(params.value, currency);
      },
      tooltipValueGetter: (params) => {
        if (!isBody(params)) return null;
        const included = params.data.shared_by.includes(name);
        return (included ? 'Remove ' : 'Add ') + name + (included ? ' from ' : ' to ') + (params.data.description || 'this expense');
      },
      cellClassRules: {
        'alloc-on': (params) => isBody(params) && params.data.shared_by.includes(name),
        'alloc-off': (params) => isBody(params) && !params.data.shared_by.includes(name),
        'alloc-payer': (params) => isBody(params) && params.data.paid_by === name,
        'net-owes': (params) => !!params.data && params.data.pinnedKind === 'net' && params.value < 0,
        'net-credit': (params) => !!params.data && params.data.pinnedKind === 'net' && params.value > 0,
      },
    };
  }

  function matrixColumns(participants, context) {
    const deleteRenderer = (params) => {
      if (!isBody(params)) return '';
      const button = root.document.createElement('button');
      button.type = 'button';
      button.className = 'grid-delete';
      button.textContent = '×';
      button.setAttribute('aria-label', 'Remove ' + (params.data.description || 'expense'));
      button.addEventListener('click', () => context.removeExpense(params.data));
      return button;
    };
    return [
      {
        colId: 'description',
        headerName: 'Item',
        field: 'description',
        pinned: 'left',
        minWidth: 170,
        flex: 1.5,
        editable: isBody,
        sortable: true,
        tooltipValueGetter: (params) => (isBody(params) && params.data.note ? 'AI note: ' + params.data.note : null),
        cellClassRules: {
          'cell-noted': (params) => isBody(params) && !!params.data.note,
          'cell-total': (params) => !isBody(params),
        },
      },
      {
        colId: 'amount',
        headerName: 'Amount',
        field: 'amount_text',
        type: 'rightAligned',
        minWidth: 108,
        width: 112,
        editable: isBody,
        sortable: true,
        comparator: (left, right) => amountCents(left) - amountCents(right),
        valueGetter: (params) => {
          if (!params.data) return null;
          if (params.data.pinnedKind) return params.data.total_cents;
          return params.data.amount_text;
        },
        valueFormatter: (params) => {
          const data = params.data;
          if (!data) return '';
          const currency = context.currency();
          if (data.pinnedKind === 'net') return (data.total_cents === 0 ? '✓ ' : '') + context.money(data.total_cents, currency);
          if (data.pinnedKind) return context.money(data.total_cents, currency);
          const cents = amountCents(data.amount_text);
          return cents ? context.money(cents, currency) : 'enter amount';
        },
        valueSetter: (params) => {
          const parsed = parseAmountInput(params.newValue);
          if (parsed === null) {
            context.toast('Enter a positive amount with at most two decimals, for example 42.50.', 'error');
            return false;
          }
          if (parsed === params.data.amount_text) return false;
          params.data.amount_text = parsed;
          return true;
        },
        cellClassRules: {
          'cell-review': (params) => isBody(params) && !amountCents(params.data.amount_text),
          'cell-mono': () => true,
        },
      },
      {
        colId: 'paid_by',
        headerName: 'Paid by',
        field: 'paid_by',
        minWidth: 120,
        width: 128,
        editable: isBody,
        sortable: true,
        cellEditor: 'agSelectCellEditor',
        cellEditorParams: { values: [''].concat(participants) },
        valueFormatter: (params) => (isBody(params) ? params.value || 'who paid?' : ''),
        cellClassRules: { 'cell-review': (params) => isBody(params) && !params.value },
      },
    ].concat(participants.map((name) => personColumn(name, context)), [
      {
        colId: 'delete',
        headerName: '',
        width: 52,
        minWidth: 52,
        maxWidth: 52,
        sortable: false,
        suppressHeaderMenuButton: true,
        cellClass: 'cell-action',
        cellRenderer: deleteRenderer,
      },
    ]);
  }

  function toggleFromCell(params, context) {
    if (!isBody(params) || !params.column) return;
    const colId = params.column.getColId();
    if (!colId.startsWith('person:')) return;
    context.toggleShare(params.data, colId.slice('person:'.length));
    params.api.refreshCells({ rowNodes: [params.node], force: true });
  }

  /** Create or update the matrix. Rows are the editor's own expense objects. */
  function renderMatrix(host, editor, split, context) {
    const names = editor.participants.slice();
    const signature = JSON.stringify(names);
    const pinned = pinnedRows(split, names);
    if (!instances.matrix) {
      instances.matrix = root.agGrid.createGrid(host, {
        theme: theme(),
        columnDefs: matrixColumns(names, context),
        rowData: editor.expenses,
        pinnedBottomRowData: pinned,
        getRowId: (params) => params.data.uid,
        domLayout: 'autoHeight',
        singleClickEdit: true,
        stopEditingWhenCellsLoseFocus: true,
        suppressMovableColumns: true,
        animateRows: true,
        tooltipShowDelay: 250,
        defaultColDef: { resizable: true, suppressHeaderMenuButton: true },
        onCellValueChanged: () => context.edited(),
        onCellClicked: (params) => toggleFromCell(params, context),
        onCellKeyDown: (params) => {
          const key = params.event && params.event.key;
          if (key === ' ' || key === 'Enter') {
            if (params.column.getColId().startsWith('person:')) params.event.preventDefault();
            toggleFromCell(params, context);
          }
        },
      });
      instances.matrixNames = signature;
      return instances.matrix;
    }
    const api = instances.matrix;
    if (signature !== instances.matrixNames) {
      api.setGridOption('columnDefs', matrixColumns(names, context));
      instances.matrixNames = signature;
    }
    api.setGridOption('rowData', editor.expenses);
    api.setGridOption('pinnedBottomRowData', pinned);
    api.refreshCells();
    return api;
  }

  /** Render the reconciliation strip with text nodes only: names are untrusted. */
  function renderRecon(host, split, editor, context) {
    const facts = reconciliation(split, editor);
    const currency = context.currency();
    const pills = [];
    if (context.isStale()) {
      pills.push(['warn', 'Not recalculated: fix the value flagged above']);
    } else if (!split) {
      pills.push(['warn', 'Add at least two people and one expense']);
    } else {
      pills.push([facts.allocated === facts.total ? 'ok' : 'warn',
        `Allocated ${context.money(facts.allocated, currency)} of ${context.money(facts.total, currency)}`]);
      if (facts.missingPayers) {
        pills.push(['warn', `Choose a payer for ${facts.missingPayers} expense${facts.missingPayers === 1 ? '' : 's'}`]);
      } else {
        pills.push([facts.netSum === 0 ? 'ok' : 'warn', `Net positions sum to ${context.money(facts.netSum, currency)}`]);
      }
      pills.push(['info', `${facts.orders} PayPal order${facts.orders === 1 ? '' : 's'} · ${context.money(facts.collecting, currency)} to collect`]);
    }
    host.replaceChildren(...pills.map(([kind, text]) => {
      const pill = root.document.createElement('span');
      pill.className = 'recon-pill recon-' + kind;
      pill.textContent = text;
      return pill;
    }));
    return facts;
  }

  function exportMatrix(context) {
    if (!instances.matrix) return;
    instances.matrix.exportDataAsCsv({
      fileName: 'splitmate-allocation.csv',
      columnKeys: instances.matrix.getColumns().map((column) => column.getColId()).filter((id) => id !== 'delete'),
      processCellCallback: (params) => {
        const colId = params.column.getColId();
        const data = params.node && params.node.data;
        if (!data) return csvSafe(params.value);
        if (colId === 'amount' || colId.startsWith('person:')) {
          const cents = colId === 'amount' && !data.pinnedKind ? amountCents(params.value) : params.value;
          if (cents === null || cents === undefined) return '';
          return (cents / 100).toFixed(2);
        }
        return csvSafe(params.value);
      },
      processHeaderCallback: (params) => csvSafe(params.column.getColDef().headerName),
    });
    context.toast('Allocation exported as CSV.', 'ok');
  }

  /* ------------------------------------------------------------ payment ledger */

  function ledgerColumns(context) {
    const statusRenderer = (params) => {
      if (!params.data || params.data.pinnedKind) return params.value || '';
      const pill = root.document.createElement('span');
      pill.className = 'status-pill status-' + String(params.data.status_key).replace(/[^a-z_]/gi, '');
      pill.textContent = params.value;
      return pill;
    };
    const mono = { cellClass: 'cell-mono', minWidth: 118, flex: 1, enableCellChangeFlash: true };
    return [
      { colId: 'name', field: 'name', headerName: 'Person', pinned: 'left', minWidth: 104, flex: 0.8, sortable: true },
      { colId: 'position', field: 'position', headerName: 'Position', minWidth: 88, width: 92, sortable: true },
      {
        colId: 'amount',
        field: 'amount_cents',
        headerName: 'Amount',
        type: 'rightAligned',
        minWidth: 96,
        width: 102,
        sortable: true,
        cellClass: 'cell-mono',
        valueFormatter: (params) => context.money(params.value || 0, context.currency()),
      },
      { colId: 'status', field: 'status', headerName: 'Status', minWidth: 170, flex: 1.3, sortable: true, enableCellChangeFlash: true, cellRenderer: statusRenderer, tooltipField: 'status' },
      { colId: 'order_id', field: 'order_id', headerName: 'PayPal order', ...mono },
      { colId: 'capture_id', field: 'capture_id', headerName: 'Capture', ...mono },
      { colId: 'capture_status', field: 'capture_status', headerName: 'Capture status', minWidth: 116, flex: 0.7, enableCellChangeFlash: true },
      { colId: 'last_error', field: 'last_error', headerName: 'Issue', minWidth: 90, flex: 0.7, tooltipField: 'last_error' },
    ];
  }

  function ledgerPinned(event) {
    const totals = event.totals || {};
    return [{
      name: 'Collected',
      pinnedKind: 'total',
      position: '',
      amount_cents: totals.captured_cents || 0,
      status: `${totals.captured_count || 0} of ${totals.order_count || 0} orders captured`,
    }];
  }

  function renderLedger(host, event, context) {
    const rows = ledgerRows(event, context.statusLabel, context.captureHeld);
    if (!instances.ledger) {
      instances.ledger = root.agGrid.createGrid(host, {
        theme: theme(),
        columnDefs: ledgerColumns(context),
        rowData: rows,
        pinnedBottomRowData: ledgerPinned(event),
        getRowId: (params) => (params.data.pinnedKind ? 'ledger-total' : 'share:' + params.data.name),
        domLayout: 'autoHeight',
        suppressMovableColumns: true,
        animateRows: true,
        tooltipShowDelay: 250,
        defaultColDef: { resizable: true, suppressHeaderMenuButton: true },
      });
      return instances.ledger;
    }
    instances.ledger.setGridOption('rowData', rows);
    instances.ledger.setGridOption('pinnedBottomRowData', ledgerPinned(event));
    return instances.ledger;
  }

  function filterLedger(text) {
    if (instances.ledger) instances.ledger.setGridOption('quickFilterText', text);
  }

  function exportLedger(context) {
    if (!instances.ledger) return;
    instances.ledger.exportDataAsCsv({
      fileName: 'splitmate-ledger.csv',
      processCellCallback: (params) => (params.column.getColId() === 'amount'
        ? ((params.value || 0) / 100).toFixed(2)
        : csvSafe(params.value)),
    });
    context.toast('Ledger exported as CSV.', 'ok');
  }

  root.SplitMateGrids = {
    available,
    parseAmountInput,
    csvSafe,
    pinnedRows,
    reconciliation,
    ledgerRows,
    matrixColumns,
    renderMatrix,
    renderRecon,
    exportMatrix,
    renderLedger,
    filterLedger,
    exportLedger,
  };
})(window);
