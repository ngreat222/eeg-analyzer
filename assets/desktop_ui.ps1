$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$q = [Console]::In.ReadToEnd() | ConvertFrom-Json
$ae = [System.Windows.Automation.AutomationElement]
$scope = [System.Windows.Automation.TreeScope]
$all = [System.Windows.Automation.Condition]::TrueCondition
$windows = $ae::RootElement.FindAll($scope::Children, $all)
if ($q.mode -eq 'windows') {
    $items = @(foreach ($window in $windows) {
        try {
            $w = $window.Current
            if ($w.ProcessId -ne $q.owner -and $w.NativeWindowHandle -and $w.Name -and !$w.IsOffscreen -and $w.ControlType.ProgrammaticName -eq 'ControlType.Window') {
                @{title=$w.Name; pid=$w.ProcessId; handle=$w.NativeWindowHandle}
            }
        } catch [System.Windows.Automation.ElementNotAvailableException] {}
    })
    ConvertTo-Json -InputObject $items -Compress
    exit
}
$root = $ae::FromHandle([intptr]$q.window.handle)
$w = $root.Current
if ($w.ProcessId -eq $q.owner -or $w.ProcessId -ne $q.window.pid -or $w.Name -cne $q.window.title -or $w.IsOffscreen) {throw 'Window changed or hidden'}
$walk = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$queue = [Collections.Generic.Queue[object]]::new()
$queue.Enqueue($root)
$items = [Collections.Generic.List[object]]::new()
$visited = 0
while ($queue.Count -and $visited++ -lt 600) {
    $node = $queue.Dequeue()
    $c = $node.Current
    if ($c.IsPassword -or $c.IsOffscreen -or $c.ProcessId -ne $w.ProcessId) {continue}
    $type = $c.ControlType.ProgrammaticName
    $action = $null
    $pattern = $null
    if ($c.IsEnabled -and $type -eq 'ControlType.Button' -and $node.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) {$action='press'}
    if ($c.IsEnabled -and $type -eq 'ControlType.Edit' -and $node.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$pattern) -and !$pattern.Current.IsReadOnly) {$action='fill'}
    if ($action) {
        $id = $node.GetRuntimeId() -join '.'
        $item = @{id=$id; name=$c.Name; role=$type; action=$action}
        if ($q.mode -eq $action -and $q.control.id -ceq $id -and $q.control.name -ceq $c.Name -and $q.control.role -ceq $type) {
            if ($action -eq 'press') {$pattern.Invoke()} else {
                $pattern.SetValue([string]$q.value)
                if ($pattern.Current.Value -cne [string]$q.value) {throw 'Text was not applied'}
            }
            '{"status":"executed"}'
            exit
        }
        $items.Add($item)
    }
    $child = $walk.GetFirstChild($node)
    while ($null -ne $child -and ($visited+$queue.Count) -lt 600) {
        $queue.Enqueue($child)
        $child = $walk.GetNextSibling($child)
    }
}
if ($q.mode -ne 'inspect') {throw 'Control changed or no longer available'}
ConvertTo-Json -InputObject @($items.ToArray()) -Compress
